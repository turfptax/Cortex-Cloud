import { useState } from 'react'
import { useVideoJob } from '../../hooks/useVideoJob'
import { isTerminal } from '../../lib/videoApi'
import { SessionStatusView } from './SessionStatusView'

/** Phase 1 batch mode — paste a video URL, get back scenes + narrative.
 *
 * Wired to cortex-vision's POST /api/video/jobs (proxied through the Hub).
 * Polls /api/video/sessions/{id} every 2s until the session reaches a
 * terminal state. Frames are served via the proxy as raw JPEGs.
 */
export function FileMode() {
  const [url, setUrl] = useState('')
  const [transcribeAudio, setTranscribeAudio] = useState(false)
  const { session, submitting, error, submit, reset } = useVideoJob()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    await submit({
      source: trimmed,
      mode: 'file',
      transcribe_audio: transcribeAudio,
    })
  }

  const isRunning = session !== null && !isTerminal(session.status)

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <form
        onSubmit={handleSubmit}
        className="bg-surface rounded-xl p-5 border border-border"
      >
        <label className="block">
          <span className="text-sm font-semibold text-text-primary">
            Process video by URL
          </span>
          <span className="block text-xs text-text-muted mt-0.5">
            YouTube, TikTok, or any source yt-dlp supports. Local file paths
            also accepted (absolute paths only).
          </span>
        </label>
        <div className="flex gap-2 mt-3">
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=..."
            disabled={isRunning || submitting}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-surface-secondary border border-border focus:border-accent focus:outline-none text-text-primary placeholder:text-text-muted disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!url.trim() || isRunning || submitting}
            className="px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-50 cursor-pointer"
          >
            {submitting ? 'Submitting…' : 'Process'}
          </button>
          {session !== null && (
            <button
              type="button"
              onClick={reset}
              className="px-3 py-2 text-sm rounded-lg border border-border text-text-secondary hover:bg-surface-tertiary cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>
        <label className="flex items-center gap-2 text-xs text-text-secondary mt-3 cursor-pointer">
          <input
            type="checkbox"
            checked={transcribeAudio}
            onChange={(e) => setTranscribeAudio(e.target.checked)}
            disabled={isRunning || submitting}
            className="w-4 h-4 rounded border-border accent-accent cursor-pointer"
          />
          Transcribe audio (extracts via ffmpeg, transcribes via Whisper —
          adds a few seconds per minute; no-op if ffmpeg or a transcription
          provider isn't configured)
        </label>
      </form>

      {error && !session && (
        <div className="bg-error/10 border border-error/30 rounded-lg p-3 text-sm text-error whitespace-pre-wrap">
          {error}
        </div>
      )}

      {session && <SessionStatusView session={session} />}
    </div>
  )
}
