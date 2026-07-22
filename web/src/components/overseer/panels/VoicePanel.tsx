import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../../lib/api'

// The voice agent is a separate pipecat sidecar the Hub launches. This panel is
// its control surface: start/stop, a link to the talk window (mic works natively
// there), and the live activity monitor embedded inline.

type AgentStatus = {
  running: boolean
  ready: boolean
  pid: number | null
  agent_url: string
  monitor_url: string
  python: string | null
}

export function VoicePanel() {
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setStatus(await apiFetch<AgentStatus>('/voice/agent/status'))
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await apiFetch<any>('/voice/agent/start', { method: 'POST' })
      if (r && r.ok === false && r.error) setError(r.error)
      await refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true)
    setError(null)
    try {
      await apiFetch('/voice/agent/stop', { method: 'POST' })
      await refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const running = !!status?.running
  const ready = !!status?.ready
  const agentUrl = status?.agent_url || 'http://localhost:7860/'
  const monitorUrl = status?.monitor_url || 'http://localhost:7861/'

  const dot = running
    ? ready
      ? 'bg-green-500'
      : 'bg-yellow-400'
    : 'bg-gray-500'
  const label = running
    ? ready
      ? 'Voice agent running'
      : 'Voice agent starting…'
    : 'Voice agent stopped'

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-block w-2.5 h-2.5 rounded-full ${dot}`} />
        <span className="text-sm text-text-secondary">{label}</span>
        {!running && (
          <button
            onClick={start}
            disabled={busy}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
          >
            Start voice agent
          </button>
        )}
        {running && (
          <>
            <a
              href={agentUrl}
              target="_blank"
              rel="noreferrer"
              className={`px-3 py-1.5 rounded-md text-xs font-medium text-white ${
                ready
                  ? 'bg-accent hover:bg-accent-hover'
                  : 'bg-accent/50 pointer-events-none'
              }`}
            >
              Open voice window
            </a>
            <button
              onClick={stop}
              disabled={busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
            >
              Stop
            </button>
          </>
        )}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <p className="text-xs text-text-muted max-w-2xl">
        Talk to Cortex and have it answer from your memory. Start the agent, open
        the voice window, allow your mic, and just talk. Chit-chat stays on a
        fast cloud model; real questions go to the overseer. Live activity shows
        below: what it heard, what it said, and every memory lookup.
      </p>

      {running && ready && (
        <div
          className="border border-border rounded-lg overflow-hidden bg-surface-secondary"
          style={{ height: 440 }}
        >
          <iframe
            title="Voice activity monitor"
            src={monitorUrl}
            className="w-full h-full"
          />
        </div>
      )}
    </div>
  )
}
