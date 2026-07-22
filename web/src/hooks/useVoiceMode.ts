/**
 * Slice 14 CP2: voice mode for the Overseer Chat tab.
 *
 * One press enters a continuous voice conversation; press again to
 * exit back to text. The loop:
 *
 *   listening → (silence detected) → transcribing → thinking
 *             → speaking → listening → …
 *
 * - listening: mic open, MediaRecorder capturing, an energy-based
 *   VAD watching for "spoke then went quiet" to end the utterance.
 * - transcribing: the clip is POSTed to /api/voice/stt (on-device
 *   whisper-cli).
 * - thinking: the transcript goes through the injected sendVoiceTurn
 *   callback (the overseer chat round-trip, voice_mode=true).
 * - speaking: the reply is spoken — browser speechSynthesis on-device
 *   by default, ElevenLabs if configured. The mic is NOT listening
 *   during playback (echo suppression).
 *
 * All audio stays on the machine unless the user opted into the
 * ElevenLabs TTS path in Settings.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export type VoiceState =
  | 'off'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking'

interface VoiceConfig {
  preferred_tts: string // 'on-device' | 'elevenlabs'
  tts: { elevenlabs_configured: boolean }
  stt: { on_device_available: boolean }
}

// ── VAD tuning ──────────────────────────────────────────────────
const SPEECH_RMS_THRESHOLD = 0.018 // above this = speech present
const SILENCE_MS_TO_END = 1200 // quiet this long after speech → end turn
const MIN_SPEECH_MS = 350 // ignore blips shorter than this
const MAX_TURN_MS = 30000 // hard cap on one utterance
const VAD_POLL_MS = 100

// ── Thinking-filler tuning (Slice 14 CP3 prelude) ───────────────
// The overseer round-trip is multi-second; if "thinking" lingers past
// this delay, speak a short on-device cue so the turn doesn't go dead.
const FILLER_DELAY_MS = 500
const THINKING_FILLERS = ['One sec.', 'Let me look that up.', 'Checking now.']

interface UseVoiceModeArgs {
  /** Send a transcript through the overseer chat (voice_mode=true)
   *  and resolve with the reply text to speak. */
  sendVoiceTurn: (text: string) => Promise<string>
}

export function useVoiceMode({ sendVoiceTurn }: UseVoiceModeArgs) {
  const [voiceState, setVoiceState] = useState<VoiceState>('off')
  const [lastError, setLastError] = useState<string | null>(null)
  const [lastHeard, setLastHeard] = useState<string>('')

  // Mutable refs — the audio graph + loop state that must not
  // trigger re-renders.
  const streamRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const vadTimerRef = useRef<number | null>(null)
  const activeRef = useRef<boolean>(false) // true between enter() and exit()
  const cfgRef = useRef<VoiceConfig | null>(null)
  const audioElRef = useRef<HTMLAudioElement | null>(null)

  // Keep a ref of voiceState so async callbacks read the latest.
  const stateRef = useRef<VoiceState>('off')
  const setState = useCallback((s: VoiceState) => {
    stateRef.current = s
    setVoiceState(s)
  }, [])

  // ── Cleanup ────────────────────────────────────────────────────
  const teardown = useCallback(() => {
    activeRef.current = false
    if (vadTimerRef.current != null) {
      window.clearInterval(vadTimerRef.current)
      vadTimerRef.current = null
    }
    try {
      recorderRef.current?.state !== 'inactive' &&
        recorderRef.current?.stop()
    } catch {
      /* noop */
    }
    recorderRef.current = null
    chunksRef.current = []
    try {
      streamRef.current?.getTracks().forEach((t) => t.stop())
    } catch {
      /* noop */
    }
    streamRef.current = null
    try {
      audioCtxRef.current?.close()
    } catch {
      /* noop */
    }
    audioCtxRef.current = null
    analyserRef.current = null
    try {
      window.speechSynthesis?.cancel()
    } catch {
      /* noop */
    }
    if (audioElRef.current) {
      audioElRef.current.pause()
      audioElRef.current = null
    }
  }, [])

  // ── TTS ────────────────────────────────────────────────────────
  const speak = useCallback(async (text: string): Promise<void> => {
    if (!text.trim()) return
    // Stop any in-progress thinking-filler before the real reply, for
    // both TTS paths (the on-device branch cancels again below).
    try {
      window.speechSynthesis?.cancel()
    } catch {
      /* noop */
    }
    const cfg = cfgRef.current
    const useEleven =
      cfg?.preferred_tts === 'elevenlabs' &&
      cfg?.tts.elevenlabs_configured
    if (useEleven) {
      try {
        const resp = await fetch('/api/voice/tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        })
        const ct = resp.headers.get('Content-Type') || ''
        if (resp.ok && ct.includes('audio')) {
          const blob = await resp.blob()
          const url = URL.createObjectURL(blob)
          await new Promise<void>((resolve) => {
            const el = new Audio(url)
            audioElRef.current = el
            el.onended = () => {
              URL.revokeObjectURL(url)
              resolve()
            }
            el.onerror = () => {
              URL.revokeObjectURL(url)
              resolve()
            }
            el.play().catch(() => resolve())
          })
          return
        }
        // Fell through (no key / not audio) → on-device fallback.
      } catch {
        /* fall through to browser TTS */
      }
    }
    // On-device: browser speechSynthesis.
    await new Promise<void>((resolve) => {
      try {
        const u = new SpeechSynthesisUtterance(text)
        u.rate = 1.05
        u.onend = () => resolve()
        u.onerror = () => resolve()
        window.speechSynthesis.cancel()
        window.speechSynthesis.speak(u)
      } catch {
        resolve()
      }
    })
  }, [])

  // ── Thinking filler (on-device, instant) ───────────────────────
  // A short spoken cue while the overseer round-trips, so a multi-
  // second reply doesn't leave dead air. Always browser speechSynthesis
  // (instant, free) regardless of the configured TTS backend. The cue
  // is a fixed canned phrase — never an invented fact.
  const speakFiller = useCallback(() => {
    try {
      const phrase =
        THINKING_FILLERS[Math.floor(Math.random() * THINKING_FILLERS.length)]
      const u = new SpeechSynthesisUtterance(phrase)
      u.rate = 1.05
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(u)
    } catch {
      /* noop */
    }
  }, [])

  // ── One full turn after a clip is captured ─────────────────────
  const handleClip = useCallback(
    async (blob: Blob) => {
      if (!activeRef.current) return
      if (blob.size < 2000) {
        // Too small to be speech — just resume listening.
        startListeningTurn()
        return
      }
      setState('transcribing')
      let text = ''
      try {
        const fd = new FormData()
        fd.append('file', blob, 'clip.webm')
        const resp = await fetch('/api/voice/stt', {
          method: 'POST',
          body: fd,
        })
        const j = await resp.json()
        text = (j?.text || '').trim()
      } catch (e: any) {
        setLastError(`Transcription failed: ${e?.message || e}`)
      }
      if (!activeRef.current) return
      if (!text) {
        // Nothing recognized — resume listening without a turn.
        startListeningTurn()
        return
      }
      setLastHeard(text)
      setState('thinking')
      // Mask the multi-second overseer round-trip: if "thinking" lingers
      // past a short delay, speak a brief on-device filler. speak(reply)
      // cancels it when the real reply is ready.
      const fillerTimer = window.setTimeout(() => {
        if (activeRef.current && stateRef.current === 'thinking') speakFiller()
      }, FILLER_DELAY_MS)
      let reply = ''
      try {
        reply = await sendVoiceTurn(text)
      } catch (e: any) {
        setLastError(`Overseer reply failed: ${e?.message || e}`)
      } finally {
        window.clearTimeout(fillerTimer)
      }
      if (!activeRef.current) return
      setState('speaking')
      if (reply) {
        await speak(reply)
      }
      if (!activeRef.current) return
      startListeningTurn()
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sendVoiceTurn, speak, speakFiller, setState],
  )

  // ── Start one listening turn ───────────────────────────────────
  const startListeningTurn = useCallback(() => {
    if (!activeRef.current) return
    const stream = streamRef.current
    const analyser = analyserRef.current
    if (!stream || !analyser) return

    setState('listening')
    chunksRef.current = []
    let recorder: MediaRecorder
    try {
      recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' })
    } catch {
      // Some browsers want no mimeType hint.
      recorder = new MediaRecorder(stream)
    }
    recorderRef.current = recorder
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
    }
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
      chunksRef.current = []
      handleClip(blob)
    }
    recorder.start()

    // VAD loop — energy-based.
    const buf = new Uint8Array(analyser.fftSize)
    let sawSpeech = false
    let speechMs = 0
    let lastSpeechAt = 0
    const turnStart = Date.now()

    if (vadTimerRef.current != null) {
      window.clearInterval(vadTimerRef.current)
    }
    vadTimerRef.current = window.setInterval(() => {
      if (!activeRef.current || recorder.state === 'inactive') return
      analyser.getByteTimeDomainData(buf)
      // RMS of the centered waveform (128 = silence midpoint).
      let sum = 0
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / buf.length)
      const now = Date.now()
      if (rms > SPEECH_RMS_THRESHOLD) {
        sawSpeech = true
        speechMs += VAD_POLL_MS
        lastSpeechAt = now
      }
      const elapsed = now - turnStart
      const silenceSince = lastSpeechAt ? now - lastSpeechAt : 0
      const endBySilence =
        sawSpeech &&
        speechMs >= MIN_SPEECH_MS &&
        silenceSince >= SILENCE_MS_TO_END
      const endByCap = elapsed >= MAX_TURN_MS
      if (endBySilence || endByCap) {
        if (vadTimerRef.current != null) {
          window.clearInterval(vadTimerRef.current)
          vadTimerRef.current = null
        }
        // recorder.stop() throws InvalidStateError if already
        // inactive — the try/catch covers that, so no state check
        // (a check trips TS control-flow narrowing from the guard
        // at the top of this callback).
        try {
          recorder.stop()
        } catch {
          /* already inactive — fine */
        }
      }
    }, VAD_POLL_MS)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleClip, setState])

  // ── Enter / exit ───────────────────────────────────────────────
  const enterVoiceMode = useCallback(async () => {
    setLastError(null)
    setLastHeard('')
    // Load backend config (which TTS/STT backends are available).
    try {
      const resp = await fetch('/api/voice/config')
      cfgRef.current = (await resp.json()) as VoiceConfig
      if (cfgRef.current && !cfgRef.current.stt.on_device_available) {
        setLastError(
          'On-device Whisper not ready — open the Journal tab and ' +
            'run one transcription to download the model first.',
        )
        return
      }
    } catch {
      // Non-fatal — proceed with on-device defaults.
      cfgRef.current = {
        preferred_tts: 'on-device',
        tts: { elevenlabs_configured: false },
        stt: { on_device_available: true },
      }
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (e: any) {
      setLastError(`Microphone access denied: ${e?.message || e}`)
      return
    }
    streamRef.current = stream
    const AudioCtx =
      window.AudioContext ||
      (window as any).webkitAudioContext
    const ctx = new AudioCtx()
    audioCtxRef.current = ctx
    const src = ctx.createMediaStreamSource(stream)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    src.connect(analyser)
    analyserRef.current = analyser
    activeRef.current = true
    startListeningTurn()
  }, [startListeningTurn])

  const exitVoiceMode = useCallback(() => {
    teardown()
    setState('off')
  }, [teardown, setState])

  // Teardown on unmount.
  useEffect(() => {
    return () => teardown()
  }, [teardown])

  return {
    voiceState,
    isVoiceActive: voiceState !== 'off',
    lastError,
    lastHeard,
    enterVoiceMode,
    exitVoiceMode,
  }
}
