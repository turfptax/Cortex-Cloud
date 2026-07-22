import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../../lib/api'
import { useCloudMode } from '../../hooks/useCloudMode'
import { PiConnectionCard } from './PiConnectionCard'
import { McpSetupCard } from './McpSetupCard'
import { LemonSyncCard } from './LemonSyncCard'
import { GeneralCard } from './GeneralCard'
import { UpdateCard } from './UpdateCard'
import { DebugLogsCard } from './DebugLogsCard'
import { PluginsTab } from './PluginsTab'
import { CloudSettingsCard } from './CloudSettingsCard'

export function SettingsPage() {
  const { cloud } = useCloudMode()
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)

  // Load config on mount. Skipped in cloud mode: there is no local
  // config, and /api/settings is not part of the cloud facade.
  useEffect(() => {
    if (cloud) {
      setLoading(false)
      return
    }
    apiFetch<{ ok: boolean; config: Record<string, unknown> }>('/settings')
      .then((res) => {
        setConfig(res.config)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [cloud])

  const handleChange = useCallback((key: string, value: unknown) => {
    setConfig((prev) => ({ ...prev, [key]: value }))
    setDirty(true)
    setSaved(false)
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await apiFetch<{ ok: boolean; config: Record<string, unknown> }>(
        '/settings',
        {
          method: 'POST',
          body: JSON.stringify(config),
        }
      )
      setConfig(res.config)
      setDirty(false)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      // save failed
    }
    setSaving(false)
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-text-muted">Loading settings...</p>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="px-6 py-4 border-b border-border flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Settings</h1>
          <p className="text-xs text-text-muted mt-0.5">
            {cloud ? 'Cortex Cloud' : 'Configure Cortex Desktop'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {!cloud && saved && (
            <span className="text-sm text-success animate-fade-in">✅ Saved!</span>
          )}
          {!cloud && dirty && (
            <span className="text-xs text-text-muted">Unsaved changes</span>
          )}
          {!cloud && (
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className="px-4 py-2 bg-accent text-white text-sm rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 cursor-pointer"
            >
              {saving ? 'Saving...' : 'Save Settings'}
            </button>
          )}
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {cloud && <CloudSettingsCard />}
        {/* First-run banner */}
        {!cloud && Boolean(config.first_run) && (
          <div className="bg-accent/10 border border-accent/30 rounded-xl p-5">
            <h2 className="text-base font-semibold text-accent mb-1">
              Welcome to Cortex Desktop!
            </h2>
            <p className="text-sm text-text-secondary">
              Let's get you connected. Scan your network to find your Pi, or enter the IP address manually below.
              Once connected, set up Claude MCP integration to control your Pi from Claude.
            </p>
          </div>
        )}

        <PiConnectionCard config={config} onChange={handleChange} />
        <McpSetupCard />
        <LemonSyncCard />
        <GeneralCard config={config} onChange={handleChange} />
        <PluginsTab />
        <UpdateCard />
        <DebugLogsCard />
      </div>
    </div>
  )
}
