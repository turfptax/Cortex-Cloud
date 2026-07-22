import { useState } from 'react'
import { apiFetch } from '../../lib/api'

interface Props {
  config: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
}

export function PiConnectionCard({ config, onChange }: Props) {
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{
    reachable: boolean
    hostname?: string
    response_ms?: number
    error?: string
  } | null>(null)

  const handleTestConnection = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await apiFetch<{
        reachable: boolean
        hostname?: string
        response_ms?: number
        error?: string
      }>('/settings/test-connection', {
        method: 'POST',
        body: JSON.stringify({
          host: config.pi_host as string,
          port: config.pi_port as number,
          username: config.pi_username as string,
          password: config.pi_password as string,
        }),
      })
      setTestResult(res)
    } catch {
      setTestResult({ reachable: false, error: 'Request failed' })
    }
    setTesting(false)
  }

  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <h2 className="text-lg font-semibold text-text-primary mb-4 flex items-center gap-2">
        <span>☁️</span> Cortex Cloud
      </h2>

      {/* Connection fields */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <label className="block text-xs text-text-muted mb-1">Core URL</label>
          <input
            type="text"
            value={(config.pi_host as string) || ''}
            onChange={(e) => onChange('pi_host', e.target.value)}
            placeholder="https://cortex.turfptax.com/core"
            className="w-full px-3 py-2 bg-surface-secondary border border-border rounded-lg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
          />
        </div>
        <div>
          <label className="block text-xs text-text-muted mb-1">Port</label>
          <input
            type="number"
            value={(config.pi_port as number) || 8420}
            onChange={(e) => onChange('pi_port', parseInt(e.target.value) || 8420)}
            className="w-full px-3 py-2 bg-surface-secondary border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-accent"
          />
        </div>
        <div>
          <label className="block text-xs text-text-muted mb-1">Username</label>
          <input
            type="text"
            value={(config.pi_username as string) || 'cortex'}
            onChange={(e) => onChange('pi_username', e.target.value)}
            className="w-full px-3 py-2 bg-surface-secondary border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-accent"
          />
        </div>
        <div>
          <label className="block text-xs text-text-muted mb-1">Service Token</label>
          <input
            type="password"
            value={(config.pi_password as string) || 'cortex'}
            onChange={(e) => onChange('pi_password', e.target.value)}
            className="w-full px-3 py-2 bg-surface-secondary border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-accent"
          />
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex gap-3 mb-4">
        <button
          onClick={handleTestConnection}
          disabled={testing || !(config.pi_host as string)}
          className="px-4 py-2 bg-accent/15 text-accent text-sm rounded-lg hover:bg-accent/25 transition-colors disabled:opacity-50 cursor-pointer"
        >
          {testing ? '⏳ Testing...' : '🧪 Test Connection'}
        </button>
      </div>

      {/* Test result */}
      {testResult && (
        <div
          className={`px-4 py-3 rounded-lg text-sm mb-4 ${
            testResult.reachable
              ? 'bg-success/10 border border-success/30 text-success'
              : 'bg-error/10 border border-error/30 text-error'
          }`}
        >
          {testResult.reachable ? (
            <span>
              ✅ Connected to <strong>{testResult.hostname}</strong> ({testResult.response_ms}ms)
            </span>
          ) : (
            <span>❌ Connection failed: {testResult.error}</span>
          )}
        </div>
      )}

    </div>
  )
}
