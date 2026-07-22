import { useEffect, useRef, useState } from 'react'
import {
  liveStart,
  liveStop,
  liveWsUrl,
  listAudioDevices,
  listCameras,
  type AudioDevice,
  type CameraInfo,
  type LiveEvent,
} from '../../lib/videoApi'

/** Phase 4 live capture mode.
 *
 * Subscribes to WS /api/video/live/ws (proxied through cortex-desktop)
 * and renders scenes as the cortex-vision sidecar emits them. Audio is
 * intentionally absent — the locked design says overseer's existing
 * audio-journal flow handles speech.
 *
 * Sessions are explicit, not implicit:
 *   - Mount fetches the camera list and renders a picker, but does NOT
 *     adopt or start any session. The user has to click Start.
 *   - Unmount stops any session this component started so navigating
 *     away never leaves a sidecar pipeline running with the camera on.
 *   - Refresh-resilience would be nice but turned out to cause more
 *     pain than it solved (auto-attaching the wrong camera_index on
 *     mount). Skipping for v0.1; revisit when there's a real
 *     long-session use case.
 */

interface SceneState {
  index: number
  thumbnail_url: string
  trigger_method: string
  similarity: number
  description?: string
  describer_model?: string
}

interface LiveStats {
  fps: number
  frames: number
  scene_count: number
  elapsed_s: number
}

interface AudioLevel {
  rms: number
  peak: number
}

type TranscribeState =
  | { kind: 'none' }
  | { kind: 'running'; message?: string }
  | { kind: 'complete'; segment_count: number; scenes_with_audio: number }
  | { kind: 'skipped'; reason?: string }
  | { kind: 'failed'; error: string }

/** Setup-form value for the audio source dropdown. Mirrors the
 * cortex-vision contract: null = video-only, "desktop" = WASAPI
 * loopback, number = sounddevice index. */
type AudioSourceChoice = null | 'desktop' | number

type Phase = 'idle' | 'starting' | 'running' | 'stopping' | 'stopped' | 'error'

export function LiveMode() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [cameras, setCameras] = useState<CameraInfo[]>([])
  const [cameraIndex, setCameraIndex] = useState(0)
  const [resolution, setResolution] = useState<[number, number]>([384, 216])
  const [pushToOverseer, setPushToOverseer] = useState(true)
  // Phase 7 audio capture (cortex-vision v0.4.0+)
  const [audioDevices, setAudioDevices] = useState<AudioDevice[]>([])
  const [audioSource, setAudioSource] = useState<AudioSourceChoice>('desktop')
  const [transcribeAudio, setTranscribeAudio] = useState(false)
  const [audioLevel, setAudioLevel] = useState<AudioLevel | null>(null)
  const [transcribe, setTranscribe] = useState<TranscribeState>({ kind: 'none' })
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [scenes, setScenes] = useState<Map<number, SceneState>>(new Map())
  const [stats, setStats] = useState<LiveStats | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  // Set to true when *we* called liveStart() in this component instance.
  // Used to gate the unmount cleanup so we don't blindly stop a
  // session some other tab might own.
  const ownsSessionRef = useRef(false)

  // Mount: fetch the camera list and pre-select a sensible default.
  // We do NOT call /live/status or attach to any pre-existing session
  // here — that turned out to create the "auto-fire with the wrong
  // camera" surprise. Starting is explicit; unmount cleans up if we
  // started something.
  useEffect(() => {
    let cancelled = false
    listCameras()
      .then((list) => {
        if (cancelled) return
        setCameras(list)
        const defaultIndex = pickDefaultCamera(list)
        if (defaultIndex !== null) setCameraIndex(defaultIndex)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    // Audio device list — best-effort. Older sidecars (pre-v0.4.0)
    // don't expose this endpoint; on 404 we silently keep an empty
    // device list and the picker falls back to a sensible default.
    listAudioDevices()
      .then((list) => {
        if (!cancelled) setAudioDevices(list)
      })
      .catch(() => {
        /* sidecar may not support audio yet — fine */
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Unmount: stop any session this component started. The browser is
  // a more invasive client than the typical web tab — leaving a live
  // capture pipeline running with the camera light on after the user
  // navigates away is bad behavior. fire-and-forget; if the stop call
  // fails we can't do much from a teardown path anyway.
  useEffect(() => {
    return () => {
      closeSocket()
      if (ownsSessionRef.current) {
        liveStop().catch(() => {
          /* best-effort teardown */
        })
        ownsSessionRef.current = false
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function openSocket(): void {
    if (wsRef.current) return
    const ws = new WebSocket(liveWsUrl())
    wsRef.current = ws

    ws.onmessage = (msg) => {
      let event: LiveEvent
      try {
        event = JSON.parse(msg.data)
      } catch {
        return
      }
      handleEvent(event)
    }

    ws.onclose = (ev) => {
      wsRef.current = null
      // Don't downgrade to 'error' if we asked to stop or it cleanly closed
      if (phase !== 'stopped' && phase !== 'stopping' && phase !== 'idle') {
        if (ev.code !== 1000 && ev.code !== 1005) {
          setError(`WebSocket closed (${ev.code}${ev.reason ? `: ${ev.reason}` : ''})`)
        }
      }
    }

    ws.onerror = () => {
      // Browser-side WS errors don't expose much; rely on onclose's code/reason
    }
  }

  function closeSocket(): void {
    if (wsRef.current) {
      try {
        wsRef.current.close(1000, 'client disconnecting')
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
  }

  function handleEvent(event: LiveEvent): void {
    switch (event.type) {
      case 'started':
        setPhase('running')
        setSessionId((event as { session_id: string }).session_id)
        break
      case 'scene': {
        const e = event as Extract<LiveEvent, { type: 'scene' }>
        setScenes((prev) => {
          const next = new Map(prev)
          const existing = next.get(e.scene_index)
          next.set(e.scene_index, {
            ...(existing ?? { index: e.scene_index }),
            index: e.scene_index,
            thumbnail_url: e.thumbnail_url,
            trigger_method: e.trigger_method,
            similarity: e.similarity,
          })
          return next
        })
        break
      }
      case 'described': {
        const e = event as Extract<LiveEvent, { type: 'described' }>
        setScenes((prev) => {
          const next = new Map(prev)
          const existing = next.get(e.scene_index)
          if (existing) {
            next.set(e.scene_index, {
              ...existing,
              description: e.description,
              describer_model: e.describer_model,
            })
          }
          return next
        })
        break
      }
      case 'stats': {
        const e = event as Extract<LiveEvent, { type: 'stats' }>
        setStats({
          fps: e.fps,
          frames: e.frames,
          scene_count: e.scene_count,
          elapsed_s: e.elapsed_s,
        })
        break
      }
      case 'stopped':
        setPhase('stopped')
        // Don't closeSocket() yet — transcription events arrive AFTER
        // stopped on cortex-vision v0.4.0+. We close once the
        // transcription terminal event lands, or immediately if no
        // transcription was requested.
        if (!transcribeAudio) {
          closeSocket()
        }
        break
      case 'error':
        setError((event as { message: string }).message ?? 'Unknown error')
        setPhase('error')
        break
      case 'audio_level': {
        const e = event as Extract<LiveEvent, { type: 'audio_level' }>
        // Always overwrite — we don't accumulate audio levels, just
        // surface the latest tick to the meter.
        setAudioLevel({ rms: e.rms ?? 0, peak: e.peak ?? 0 })
        break
      }
      case 'transcribing': {
        const e = event as Extract<LiveEvent, { type: 'transcribing' }>
        setTranscribe({ kind: 'running', message: e.message })
        break
      }
      case 'transcribed': {
        const e = event as Extract<LiveEvent, { type: 'transcribed' }>
        setTranscribe({
          kind: 'complete',
          segment_count: e.segment_count ?? 0,
          scenes_with_audio: e.scenes_with_audio ?? 0,
        })
        closeSocket()
        break
      }
      case 'transcribe_skipped': {
        const e = event as Extract<LiveEvent, { type: 'transcribe_skipped' }>
        setTranscribe({ kind: 'skipped', reason: e.reason })
        closeSocket()
        break
      }
      case 'transcribe_failed': {
        const e = event as Extract<LiveEvent, { type: 'transcribe_failed' }>
        setTranscribe({
          kind: 'failed',
          error: e.error || e.message || 'Transcription failed',
        })
        closeSocket()
        break
      }
      default:
        // Forward-compatible: ignore unknown event types
        break
    }
  }

  const handleStart = async () => {
    setError(null)
    setPhase('starting')
    setScenes(new Map())
    setStats(null)
    setAudioLevel(null)
    setTranscribe({ kind: 'none' })
    try {
      await liveStart({
        camera_index: cameraIndex,
        resolution,
        audio_source: audioSource,
        transcribe_audio: transcribeAudio,
        // The push_to_overseer flag isn't accepted by /live/start in
        // Phase 4 — the bridge picks live sessions up via polling per
        // cortex-vision's bridge contract. Toggle is rendered for the
        // user but applied at the bridge layer once Phase 6 lands.
      })
      ownsSessionRef.current = true
      openSocket()
    } catch (e) {
      // cortex-vision returns a structured 503 with a clean message
      // when the camera fails to open; surface as a prominent error
      // banner rather than a small toast. Same for 409 (session
      // already running) — give the user enough to act on.
      setError(parseStartError(e))
      setPhase('error')
    }
  }

  const handleStop = async () => {
    setPhase('stopping')
    try {
      await liveStop()
      ownsSessionRef.current = false
      // The "stopped" event over WS finalizes the phase; if the WS is
      // already gone, force-finalize here.
      if (!wsRef.current) setPhase('stopped')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }

  const handleReset = () => {
    closeSocket()
    ownsSessionRef.current = false
    setPhase('idle')
    setError(null)
    setSessionId(null)
    setScenes(new Map())
    setStats(null)
    setAudioLevel(null)
    setTranscribe({ kind: 'none' })
  }

  const sceneList = Array.from(scenes.values()).sort(
    (a, b) => b.index - a.index, // newest first
  )

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Prominent error banner — sticks above whatever phase card is
          showing. Replaces the easy-to-miss thin amber strip. */}
      {error && phase !== 'error' && (
        <div className="bg-error/10 border border-error/40 rounded-lg p-4 flex items-start gap-3">
          <span className="text-error text-lg leading-none">⚠</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-error">
              Live mode error
            </p>
            <p className="text-xs text-error/80 mt-1 whitespace-pre-wrap">
              {error}
            </p>
          </div>
          <button
            onClick={() => setError(null)}
            className="text-text-muted hover:text-text-primary text-sm cursor-pointer"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}

      {phase === 'idle' && (
        <SetupCard
          cameras={cameras}
          cameraIndex={cameraIndex}
          setCameraIndex={setCameraIndex}
          resolution={resolution}
          setResolution={setResolution}
          pushToOverseer={pushToOverseer}
          setPushToOverseer={setPushToOverseer}
          audioDevices={audioDevices}
          audioSource={audioSource}
          setAudioSource={setAudioSource}
          transcribeAudio={transcribeAudio}
          setTranscribeAudio={setTranscribeAudio}
          onStart={handleStart}
        />
      )}

      {phase === 'starting' && (
        <BannerCard
          dotClass="bg-amber-500"
          title="Starting live session…"
          subtitle="Opening camera, attaching detector + describer threads"
        />
      )}

      {(phase === 'running' || phase === 'stopping' || phase === 'stopped') && (
        <>
          <RunningHeader
            phase={phase}
            sessionId={sessionId}
            stats={stats}
            onStop={handleStop}
            onReset={handleReset}
            audioLevel={audioLevel}
            audioEnabled={audioSource !== null}
          />
          <TranscribeBanner state={transcribe} />
          <SceneGrid scenes={sceneList} />
        </>
      )}

      {phase === 'error' && (
        <div className="bg-error/10 border border-error/30 rounded-lg p-4">
          <p className="text-sm font-semibold text-error">Live session failed</p>
          {error && (
            <p className="text-xs text-error/80 mt-1 whitespace-pre-wrap">
              {error}
            </p>
          )}
          <button
            onClick={handleReset}
            className="mt-3 px-3 py-1.5 text-xs rounded border border-error/40 text-error hover:bg-error/10 cursor-pointer"
          >
            Reset
          </button>
        </div>
      )}

    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SetupCard({
  cameras,
  cameraIndex,
  setCameraIndex,
  resolution,
  setResolution,
  pushToOverseer,
  setPushToOverseer,
  audioDevices,
  audioSource,
  setAudioSource,
  transcribeAudio,
  setTranscribeAudio,
  onStart,
}: {
  cameras: CameraInfo[]
  cameraIndex: number
  setCameraIndex: (i: number) => void
  resolution: [number, number]
  setResolution: (r: [number, number]) => void
  pushToOverseer: boolean
  setPushToOverseer: (b: boolean) => void
  audioDevices: AudioDevice[]
  audioSource: AudioSourceChoice
  setAudioSource: (s: AudioSourceChoice) => void
  transcribeAudio: boolean
  setTranscribeAudio: (b: boolean) => void
  onStart: () => void
}) {
  const resOptions: Array<[string, [number, number]]> = [
    ['384 × 216 (default — fastest)', [384, 216]],
    ['640 × 360', [640, 360]],
    ['1280 × 720', [1280, 720]],
  ]
  return (
    <div className="bg-surface rounded-xl p-5 border border-border">
      <h2 className="text-sm font-semibold text-text-primary">
        Watch screen live (OBS Virtual Camera or capture device)
      </h2>
      <p className="text-xs text-text-muted mt-0.5">
        Continuous capture. The detector emits a scene event each time the
        screen meaningfully changes; the describer pushes a description as
        soon as the LLM finishes. Stats stream every second.
      </p>

      <div className="space-y-3 mt-4">
        <div>
          <label className="text-xs uppercase tracking-wide text-text-muted block mb-1">
            Camera
          </label>
          {cameras.length > 0 ? (
            <select
              value={cameraIndex}
              onChange={(e) => setCameraIndex(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
            >
              {cameras.map((c) => (
                <option key={c.index} value={c.index}>
                  {formatCameraLabel(c)}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="number"
              min={0}
              value={cameraIndex}
              onChange={(e) => setCameraIndex(Number(e.target.value))}
              className="w-32 px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
            />
          )}
          {cameras.length === 0 && (
            <p className="text-[11px] text-text-muted mt-1">
              No cameras enumerated. OBS Virtual Camera is usually index 0
              when it's the only one running.
            </p>
          )}
        </div>

        <div>
          <label className="text-xs uppercase tracking-wide text-text-muted block mb-1">
            Capture resolution
          </label>
          <select
            value={`${resolution[0]}x${resolution[1]}`}
            onChange={(e) => {
              const opt = resOptions.find(
                ([, r]) => `${r[0]}x${r[1]}` === e.target.value,
              )
              if (opt) setResolution(opt[1])
            }}
            className="w-full px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
          >
            {resOptions.map(([label, r]) => (
              <option key={label} value={`${r[0]}x${r[1]}`}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs uppercase tracking-wide text-text-muted block mb-1">
            Audio source
          </label>
          <select
            value={audioSourceToOption(audioSource)}
            onChange={(e) => setAudioSource(audioSourceFromOption(e.target.value))}
            className="w-full px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border text-text-primary focus:border-accent focus:outline-none"
          >
            <option value="null">None (video only)</option>
            <option value="desktop">Desktop audio (WASAPI loopback)</option>
            {audioDevices
              .filter((d) => d.kind !== 'desktop' && typeof d.index === 'number')
              .map((d) => (
                <option key={`idx-${d.index}`} value={`idx-${d.index}`}>
                  {formatAudioDeviceLabel(d)}
                </option>
              ))}
          </select>
          <p className="text-[11px] text-text-muted mt-1">
            Desktop audio captures whatever's playing on your system.
            Pick a real input for a microphone or line-in source.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={transcribeAudio}
            disabled={audioSource === null}
            onChange={(e) => setTranscribeAudio(e.target.checked)}
            className="w-4 h-4 rounded border-border accent-accent cursor-pointer disabled:opacity-50"
          />
          <span className={audioSource === null ? 'opacity-50' : ''}>
            Transcribe audio after Stop (runs whisper, adds a few seconds
            per minute of capture)
          </span>
        </label>

        <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={pushToOverseer}
            onChange={(e) => setPushToOverseer(e.target.checked)}
            className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
          />
          Push 5-min rollups to overseer (handled by the bridge)
        </label>
      </div>

      <button
        onClick={onStart}
        className="mt-4 px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover cursor-pointer"
      >
        Start live session
      </button>
    </div>
  )
}

function BannerCard({
  dotClass,
  title,
  subtitle,
}: {
  dotClass: string
  title: string
  subtitle?: string
}) {
  return (
    <div className="bg-surface rounded-xl p-4 border border-border flex items-center gap-3">
      <span className={`w-2.5 h-2.5 rounded-full ${dotClass} animate-pulse`} />
      <div>
        <p className="text-sm font-semibold text-text-primary">{title}</p>
        {subtitle && (
          <p className="text-xs text-text-muted mt-0.5">{subtitle}</p>
        )}
      </div>
    </div>
  )
}

function RunningHeader({
  phase,
  sessionId,
  stats,
  onStop,
  onReset,
  audioLevel,
  audioEnabled,
}: {
  phase: Phase
  sessionId: string | null
  stats: LiveStats | null
  onStop: () => void
  onReset: () => void
  audioLevel: AudioLevel | null
  audioEnabled: boolean
}) {
  const live = phase === 'running'
  return (
    <div className="bg-surface rounded-xl p-4 border border-border">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={`w-2.5 h-2.5 rounded-full ${
              live ? 'bg-error animate-pulse' : 'bg-text-muted'
            }`}
          />
          <div>
            <p className="text-sm font-semibold text-text-primary">
              {live
                ? 'Live'
                : phase === 'stopping'
                  ? 'Stopping…'
                  : 'Stopped'}
            </p>
            {sessionId && (
              <p className="text-[11px] text-text-muted font-mono">
                {sessionId.slice(0, 8)}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {live && (
            <button
              onClick={onStop}
              className="px-4 py-2 text-sm rounded-lg bg-error text-white hover:bg-error/90 cursor-pointer"
            >
              Stop
            </button>
          )}
          {phase === 'stopped' && (
            <button
              onClick={onReset}
              className="px-3 py-2 text-sm rounded-lg border border-border text-text-secondary hover:bg-surface-tertiary cursor-pointer"
            >
              Done
            </button>
          )}
        </div>
      </div>

      {stats && (
        <div className="mt-3 grid grid-cols-4 gap-3 text-center">
          <Stat label="FPS" value={stats.fps.toFixed(1)} />
          <Stat label="Frames" value={stats.frames.toLocaleString()} />
          <Stat label="Scenes" value={String(stats.scene_count)} />
          <Stat label="Elapsed" value={fmtElapsed(stats.elapsed_s)} />
        </div>
      )}

      {audioEnabled && (
        <div className="mt-3">
          <AudioMeter level={audioLevel} live={phase === 'running'} />
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-secondary rounded-md p-2">
      <p className="text-sm font-mono text-text-primary">{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-text-muted">
        {label}
      </p>
    </div>
  )
}

function SceneGrid({ scenes }: { scenes: SceneState[] }) {
  if (scenes.length === 0) {
    return (
      <div className="bg-surface rounded-xl p-8 border border-border text-center">
        <p className="text-sm text-text-muted">
          Watching for scene changes…
        </p>
      </div>
    )
  }
  return (
    <div className="bg-surface rounded-xl p-4 border border-border space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
        Scenes ({scenes.length}, newest first)
      </h3>
      <div className="space-y-3">
        {scenes.map((s) => (
          <SceneCard key={s.index} scene={s} />
        ))}
      </div>
    </div>
  )
}

function SceneCard({ scene }: { scene: SceneState }) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex gap-3">
        <img
          src={scene.thumbnail_url}
          alt={`Scene ${scene.index}`}
          className="w-32 h-20 object-cover rounded shrink-0 bg-surface-tertiary"
          loading="lazy"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-text-muted">
              #{scene.index}
            </span>
            <span className="text-[10px] uppercase tracking-wide text-text-muted opacity-70">
              {scene.trigger_method} · sim {scene.similarity.toFixed(3)}
            </span>
            {scene.describer_model && (
              <span className="text-[10px] text-text-muted opacity-60 font-mono">
                {scene.describer_model}
              </span>
            )}
          </div>
          {scene.description ? (
            <p className="text-sm text-text-secondary mt-1 leading-snug">
              {scene.description}
            </p>
          ) : (
            <p className="text-xs text-text-muted italic mt-1">
              describing…
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

/** Format one camera as "Camera 2 — 1920×1080 @ 30fps" per the spec.
 * Falls back gracefully when the sidecar omits any of the optional
 * fields (missing native_resolution, fps==0, etc.). */
function formatCameraLabel(c: CameraInfo): string {
  const parts = [`Camera ${c.index}`]
  if (c.name) parts.push(c.name)
  if (c.native_resolution) {
    const [w, h] = c.native_resolution
    let res = `${w}×${h}`
    if (c.native_fps && c.native_fps > 0) {
      res += ` @ ${Math.round(c.native_fps)}fps`
    }
    parts.push(res)
  }
  return parts.join(' — ')
}

/** Pick a sensible default camera. Prefer:
 *   1. An entry whose name explicitly says "OBS"
 *   2. The highest-resolution entry (≥1280 wide is the OBS Virtual
 *      Camera signature on a typical desktop — it mirrors the user's
 *      primary monitor)
 *   3. Index 0 as a last resort
 *
 * Returns the chosen camera_index, or null if the list is empty.
 */
function pickDefaultCamera(cameras: CameraInfo[]): number | null {
  if (cameras.length === 0) return null

  const named = cameras.find((c) =>
    typeof c.name === 'string' && /obs/i.test(c.name),
  )
  if (named) return named.index

  // Highest-resolution; fall back to lowest index on ties
  let best: CameraInfo | null = null
  for (const c of cameras) {
    if (!c.native_resolution) continue
    if (
      best === null ||
      !best.native_resolution ||
      c.native_resolution[0] > best.native_resolution[0]
    ) {
      best = c
    }
  }
  if (best && best.native_resolution && best.native_resolution[0] >= 1280) {
    return best.index
  }

  // Nothing 1280+; just return the first one to avoid silently picking
  // index 0 when index 0 is e.g. a phone via DroidCam.
  return cameras[0].index
}

/** Render a /live/start error in human-readable form. cortex-vision's
 * error path returns the familiar FastAPI {"detail": "..."} shape. */
function parseStartError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  // apiFetch wraps non-2xx as `Error: API error <code>: <body>` — pull
  // a JSON detail field out if present so the user sees just the
  // useful sentence.
  const match = msg.match(/API error (\d+): (.+)$/s)
  if (!match) return msg
  const [, code, body] = match
  try {
    const parsed = JSON.parse(body)
    const detail = typeof parsed.detail === 'string'
      ? parsed.detail
      : typeof parsed.detail?.message === 'string'
        ? parsed.detail.message
        : null
    if (detail) return `${detail} (HTTP ${code})`
  } catch {
    /* not json; fall through */
  }
  return msg
}

/** Format an audio device for the picker. Falls back gracefully when
 * cortex-vision omits sample rate / channels (older sidecars). */
function formatAudioDeviceLabel(d: AudioDevice): string {
  const parts: string[] = [d.name || `Input ${d.index}`]
  if (typeof d.channels === 'number') parts.push(`${d.channels}ch`)
  if (typeof d.default_sample_rate === 'number') {
    parts.push(`${Math.round(d.default_sample_rate / 1000)}kHz`)
  }
  return parts.join(' · ')
}

/** Marshal AudioSourceChoice to a string suitable for <select value>.
 * The dropdown uses string values: "null", "desktop", or "idx-N". */
function audioSourceToOption(c: AudioSourceChoice): string {
  if (c === null) return 'null'
  if (c === 'desktop') return 'desktop'
  return `idx-${c}`
}

function audioSourceFromOption(v: string): AudioSourceChoice {
  if (v === 'null') return null
  if (v === 'desktop') return 'desktop'
  if (v.startsWith('idx-')) return Number(v.slice(4))
  return null
}

/** Audio level meter — RMS as the main fill, peak as a thin marker.
 * Updates at the rate of the audio_level events (~10 Hz from the
 * sidecar). isolates re-render cost: only this component re-renders
 * on each level update; the parent wraps it in a stable host. */
function AudioMeter({
  level,
  live,
}: {
  level: AudioLevel | null
  live: boolean
}) {
  const rmsPct = Math.min(100, Math.max(0, (level?.rms ?? 0) * 100))
  const peakPct = Math.min(100, Math.max(0, (level?.peak ?? 0) * 100))
  // Color-code: green at low/mid, amber near clip, red at clip
  const fillColor =
    rmsPct > 90
      ? 'bg-error'
      : rmsPct > 70
        ? 'bg-amber-500'
        : 'bg-success'
  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[10px] uppercase tracking-wide text-text-muted">
          Audio
        </span>
        <span className="text-[10px] font-mono text-text-muted">
          {level ? `rms ${level.rms.toFixed(2)} · peak ${level.peak.toFixed(2)}` : '— silent —'}
        </span>
      </div>
      <div className="relative h-2 bg-surface-tertiary rounded-full overflow-hidden">
        <div
          className={`h-full ${fillColor} ${live ? 'transition-[width] duration-75' : ''}`}
          style={{ width: `${rmsPct}%` }}
        />
        {peakPct > 0 && (
          <div
            className="absolute top-0 h-full w-0.5 bg-text-primary/70"
            style={{ left: `calc(${peakPct}% - 1px)` }}
          />
        )}
      </div>
    </div>
  )
}

/** Surface for the post-Stop transcription lifecycle. Only renders
 * when there's something to say — silent during the regular
 * running phase. */
function TranscribeBanner({ state }: { state: TranscribeState }) {
  if (state.kind === 'none') return null
  if (state.kind === 'running') {
    return (
      <div className="bg-accent/10 border border-accent/30 rounded-lg p-3 flex items-center gap-3">
        <span className="w-2.5 h-2.5 rounded-full bg-accent animate-pulse" />
        <div>
          <p className="text-sm font-semibold text-accent-hover">
            Transcribing audio…
          </p>
          <p className="text-xs text-text-muted mt-0.5">
            {state.message ??
              'Running whisper on the captured audio. Adds a few seconds per minute of session.'}
          </p>
        </div>
      </div>
    )
  }
  if (state.kind === 'complete') {
    return (
      <div className="bg-success/10 border border-success/30 rounded-lg p-3">
        <p className="text-sm font-semibold text-success">
          Transcribed: {state.segment_count} segment
          {state.segment_count === 1 ? '' : 's'}
          {state.scenes_with_audio > 0 &&
            ` matched to ${state.scenes_with_audio} scene${state.scenes_with_audio === 1 ? '' : 's'}`}
        </p>
      </div>
    )
  }
  if (state.kind === 'skipped') {
    return (
      <div className="bg-text-muted/10 border border-border rounded-lg p-3 text-xs text-text-muted">
        Transcription skipped{state.reason ? `: ${state.reason}` : ''}.
      </div>
    )
  }
  // failed
  return (
    <div className="bg-error/10 border border-error/30 rounded-lg p-3">
      <p className="text-sm font-semibold text-error">Transcription failed</p>
      <p className="text-xs text-error/80 mt-0.5 whitespace-pre-wrap">
        {state.error}
      </p>
    </div>
  )
}

function fmtElapsed(s: number): string {
  const total = Math.floor(s)
  const m = Math.floor(total / 60)
  const sec = total % 60
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return `${h}:${(m % 60).toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`
  }
  return `${m}:${sec.toString().padStart(2, '0')}`
}
