import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../../lib/api'

interface LogsResponse {
  lines: string[]
  total: number
  log_file: string
}

export function DebugLogsCard() {
  const [logs, setLogs] = useState<string[]>([])
  const [logFile, setLogFile] = useState('')
  const [open, setOpen] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const fetchLogs = useCallback(async () => {
    try {
      const res = await apiFetch<LogsResponse>('/debug/logs?tail=200')
      if (res?.lines) {
        setLogs(res.lines)
        setLogFile(res.log_file)
      }
    } catch {
      setLogs(['Failed to fetch logs'])
    }
  }, [])

  useEffect(() => {
    if (open) fetchLogs()
  }, [open, fetchLogs])

  useEffect(() => {
    if (!open || !autoRefresh) return
    const id = setInterval(fetchLogs, 3000)
    return () => clearInterval(id)
  }, [open, autoRefresh, fetchLogs])

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs, open])

  return (
    <div className="bg-surface rounded-xl border border-border overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-5 py-4 flex items-center justify-between cursor-pointer hover:bg-surface-hover transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="text-lg">🔧</span>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-text-primary">Debug Logs</h3>
            <p className="text-xs text-text-muted">Backend log output</p>
          </div>
        </div>
        <span className="text-text-muted text-sm">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="border-t border-border">
          <div className="px-5 py-2 flex items-center justify-between bg-surface-hover/50">
            <span className="text-xs text-text-muted font-mono truncate max-w-[60%]">{logFile}</span>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-text-muted cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="rounded"
                />
                Auto-refresh
              </label>
              <button
                onClick={fetchLogs}
                className="text-xs text-accent hover:text-accent-hover cursor-pointer"
              >
                Refresh
              </button>
            </div>
          </div>
          <div className="max-h-80 overflow-y-auto bg-black/30 p-3">
            <pre className="text-xs font-mono text-text-secondary whitespace-pre-wrap break-all leading-5">
              {logs.length > 0 ? logs.join('\n') : 'No logs yet'}
            </pre>
            <div ref={bottomRef} />
          </div>
        </div>
      )}
    </div>
  )
}
