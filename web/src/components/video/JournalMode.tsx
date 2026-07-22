import { useEffect, useRef, useState } from 'react'
import { useVideoJob } from '../../hooks/useVideoJob'
import { isTerminal } from '../../lib/videoApi'
import { SessionStatusView } from './SessionStatusView'

/** Phase 3 video journal mode.
 *
 * Browser-side capture via getDisplayMedia() (screen) + optional
 * getUserMedia() (mic). Recording goes through MediaRecorder using the
 * highest-quality webm codec the browser supports (vp9+opus first,
 * fallback chain below). On stop the blob POSTs to
 * /api/video/jobs/upload with mode=journal; the same polling pattern
 * as FileMode picks it up from there.
 *
 * Audio transcription is intentionally NOT requested — overseer's
 * existing audio-journal flow handles speech, and Phase 6 of
 * cortex-vision is where the bridge attaches video scenes alongside.
 *
 * Phase requirements (Permissions Policy + browser support):
 *   - getDisplayMedia: Chromium / Edge / Firefox latest. Triggered
 *     by user click; the browser shows a screen-picker.
 *   - MediaRecorder: same browsers. iOS Safari does not support
 *     screen recording from the web — desktop only for now.
 */

const PREFERRED_MIME_TYPES = [
  'video/webm;codecs=vp9,opus',
  'video/webm;codecs=vp8,opus',
  'video/webm;codecs=vp9',
  'video/webm;codecs=vp8',
  'video/webm',
  'video/mp4',
] as const

function pickMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return 'video/webm'
  for (const m of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(m)) return m
  }
  return 'video/webm'
}

type Phase = 'idle' | 'recording' | 'uploading' | 'tracking'

export function JournalMode() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [includeMic, setIncludeMic] = useState(true)
  const [pushToOverseer, setPushToOverseer] = useState(true)
  const [transcribeAudio, setTranscribeAudio] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [uploadProgress, setUploadProgress] = useState({ loaded: 0, total: 0 })

  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const captureStreamRef = useRef<MediaStream | null>(null)
  const previewVideoRef = useRef<HTMLVideoElement | null>(null)
  const startedAtRef = useRef<number>(0)
  const tickTimerRef = useRef<number | null>(null)

  const { session, submitUpload, error: jobError, reset: resetJob } =
    useVideoJob()

  // Tick the recording timer
  useEffect(() => {
    if (phase !== 'recording') return
    const tick = () => {
      setElapsedMs(Date.now() - startedAtRef.current)
      tickTimerRef.current = window.setTimeout(tick, 250)
    }
    tick()
    return () => {
      if (tickTimerRef.current !== null) window.clearTimeout(tickTimerRef.current)
    }
  }, [phase])

  // Cleanup on unmount: stop streams + recorder
  useEffect(() => {
    return () => {
      stopStream(captureStreamRef.current)
      captureStreamRef.current = null
      if (
        recorderRef.current &&
        recorderRef.current.state === 'recording'
      ) {
        try {
          recorderRef.current.stop()
        } catch {
          /* ignore */
        }
      }
    }
  }, [])

  const startRecording = async () => {
    setErrorMsg(null)
    if (typeof navigator.mediaDevices?.getDisplayMedia !== 'function') {
      setErrorMsg(
        'This browser does not support screen recording (getDisplayMedia missing).'
      )
      return
    }

    try {
      // 1. Screen capture (system picker)
      const display = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: false,
      })

      // 2. Optional microphone
      let mic: MediaStream | null = null
      if (includeMic) {
        try {
          mic = await navigator.mediaDevices.getUserMedia({
            audio: true,
            video: false,
          })
        } catch (e) {
          // Mic refused / unavailable — continue without it
          console.warn('mic capture failed; proceeding screen-only:', e)
        }
      }

      // 3. Combine into one stream so MediaRecorder produces one file
      const combined = new MediaStream()
      display.getTracks().forEach((t) => combined.addTrack(t))
      mic?.getAudioTracks().forEach((t) => combined.addTrack(t))

      // 4. If the user stops the share via the browser's own UI, end recording
      const screenTrack = display.getVideoTracks()[0]
      if (screenTrack) {
        screenTrack.addEventListener('ended', () => {
          if (recorderRef.current?.state === 'recording') {
            stopRecording()
          }
        })
      }

      captureStreamRef.current = combined
      if (previewVideoRef.current) {
        previewVideoRef.current.srcObject = combined
      }

      const mime = pickMimeType()
      const recorder = new MediaRecorder(combined, { mimeType: mime })
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => handleStop(mime)
      recorder.onerror = (e) => {
        console.error('MediaRecorder error', e)
        setErrorMsg(`Recording error: ${(e as Event).type}`)
      }

      recorderRef.current = recorder
      startedAtRef.current = Date.now()
      setElapsedMs(0)
      recorder.start(1_000) // emit a chunk every second so the buffer never grows unbounded
      setPhase('recording')
    } catch (e) {
      // User cancelled the picker, etc.
      const msg = e instanceof Error ? e.message : String(e)
      if (!/permission denied|user cancelled|aborted/i.test(msg)) {
        setErrorMsg(msg)
      }
    }
  }

  const stopRecording = () => {
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      recorderRef.current.stop()
    }
  }

  const handleStop = async (mime: string) => {
    setPhase('uploading')
    stopStream(captureStreamRef.current)
    captureStreamRef.current = null

    const ext = mimeToExt(mime)
    const blob = new Blob(chunksRef.current, { type: mime })
    const filename = `journal-${new Date().toISOString().replace(/[:.]/g, '-')}.${ext}`

    setUploadProgress({ loaded: 0, total: blob.size })
    const sessionId = await submitUpload(blob, filename, {
      mode: 'journal',
      push_to_overseer: pushToOverseer,
      transcribe_audio: transcribeAudio,
      onProgress: (loaded, total) => setUploadProgress({ loaded, total }),
    })

    if (sessionId) {
      setPhase('tracking')
    } else {
      setPhase('idle')
    }
  }

  const handleClear = () => {
    resetJob()
    setPhase('idle')
    setElapsedMs(0)
    setErrorMsg(null)
    setUploadProgress({ loaded: 0, total: 0 })
  }

  const showSession = session !== null && phase === 'tracking'
  const sessionDone = session !== null && isTerminal(session.status)

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      {/* Setup card */}
      {phase === 'idle' && (
        <div className="bg-surface rounded-xl p-5 border border-border">
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-text-primary">
              Record a video journal
            </h2>
            <p className="text-xs text-text-muted mt-0.5">
              Captures your screen via the browser. Optionally includes mic
              audio. The recording uploads to the Cortex Vision sidecar and
              gets processed into scenes with descriptions.
            </p>
          </div>

          <div className="space-y-2 mb-4">
            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={includeMic}
                onChange={(e) => setIncludeMic(e.target.checked)}
                className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
              />
              Include microphone
            </label>
            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={pushToOverseer}
                onChange={(e) => setPushToOverseer(e.target.checked)}
                className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
              />
              Push scenes to today's overseer journal when complete
            </label>
            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={transcribeAudio}
                onChange={(e) => setTranscribeAudio(e.target.checked)}
                className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
              />
              Transcribe audio (Whisper)
            </label>
          </div>

          <button
            onClick={startRecording}
            className="px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover cursor-pointer"
          >
            Start recording
          </button>
        </div>
      )}

      {/* Recording state */}
      {phase === 'recording' && (
        <div className="bg-surface rounded-xl p-5 border border-border space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-error animate-pulse" />
              <span className="text-sm font-semibold text-text-primary">
                Recording
              </span>
              <span className="text-sm text-text-muted font-mono">
                {fmtElapsed(elapsedMs)}
              </span>
            </div>
            <button
              onClick={stopRecording}
              className="px-4 py-2 text-sm rounded-lg bg-error text-white hover:bg-error/90 cursor-pointer"
            >
              Stop recording
            </button>
          </div>
          <video
            ref={previewVideoRef}
            autoPlay
            muted
            playsInline
            className="w-full max-h-96 bg-surface-tertiary rounded-lg"
          />
          <p className="text-xs text-text-muted">
            Stopping the screen share via the browser bar will also end the
            recording.
          </p>
        </div>
      )}

      {/* Uploading state */}
      {phase === 'uploading' && (
        <div className="bg-surface rounded-xl p-5 border border-border">
          <div className="flex items-center gap-3">
            <span className="w-2.5 h-2.5 rounded-full bg-accent animate-pulse" />
            <span className="text-sm font-semibold text-text-primary">
              Uploading…
            </span>
            <span className="text-xs text-text-muted ml-auto">
              {fmtBytes(uploadProgress.loaded)}
              {uploadProgress.total > 0
                ? ` / ${fmtBytes(uploadProgress.total)}`
                : ''}
            </span>
          </div>
          <div className="mt-3 h-1.5 bg-surface-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-200"
              style={{
                width:
                  uploadProgress.total > 0
                    ? `${Math.min(100, (uploadProgress.loaded / uploadProgress.total) * 100)}%`
                    : '0%',
              }}
            />
          </div>
        </div>
      )}

      {/* Errors */}
      {(errorMsg || jobError) && phase === 'idle' && (
        <div className="bg-error/10 border border-error/30 rounded-lg p-3 text-sm text-error whitespace-pre-wrap">
          {errorMsg || jobError}
        </div>
      )}

      {/* Pipeline tracking */}
      {showSession && session && (
        <>
          <SessionStatusView session={session} />
          {sessionDone && (
            <div className="flex justify-end">
              <button
                onClick={handleClear}
                className="px-3 py-2 text-sm rounded-lg border border-border text-text-secondary hover:bg-surface-tertiary cursor-pointer"
              >
                Record another
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function stopStream(stream: MediaStream | null): void {
  if (!stream) return
  stream.getTracks().forEach((t) => {
    try {
      t.stop()
    } catch {
      /* ignore */
    }
  })
}

function fmtElapsed(ms: number): string {
  const total = Math.floor(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function mimeToExt(mime: string): string {
  if (mime.startsWith('video/webm')) return 'webm'
  if (mime.startsWith('video/mp4')) return 'mp4'
  return 'webm'
}
