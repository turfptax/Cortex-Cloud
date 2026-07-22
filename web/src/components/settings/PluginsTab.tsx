import { useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'
import {
  useInstalledPlugins,
  type InstalledPlugin,
  type MarketplacePlugin,
} from '../../hooks/useInstalledPlugins'
import { CortexVisionConfigForm } from './CortexVisionConfigForm'

/** Plugins that expose their own Configure form. Today only
 * cortex-vision; future plugins would register here. */
const CONFIGURABLE_PLUGINS = new Set(['cortex-vision'])

/** Settings → Plugins card.
 *
 * Phase 0 surface:
 *  - Installed list with status dot + version + variant + Restart/Uninstall
 *  - Marketplace list with Install button (501 stub — surfaces the
 *    documented hand-edit-registry.json flow as the workaround)
 *
 * Real install/update wiring lands in Phase 5 of the v0.18 cycle.
 */
export function PluginsTab() {
  const { plugins, loading, error, refresh } = useInstalledPlugins()
  const [marketplace, setMarketplace] = useState<MarketplacePlugin[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [configuringId, setConfiguringId] = useState<string | null>(null)
  const [checkingUpdates, setCheckingUpdates] = useState(false)

  useEffect(() => {
    apiFetch<MarketplacePlugin[]>('/plugins/marketplace')
      .then(setMarketplace)
      .catch(() => setMarketplace([]))
  }, [plugins.length])

  const handleRestart = async (id: string) => {
    setBusyId(id)
    setActionMessage(null)
    try {
      await apiFetch(`/plugins/${id}/restart`, { method: 'POST' })
      setActionMessage(`Restarted ${id}`)
      await refresh()
    } catch (e) {
      setActionMessage(
        `Restart failed: ${e instanceof Error ? e.message : String(e)}`
      )
    }
    setBusyId(null)
  }

  const handleUninstall = async (id: string) => {
    if (!confirm(`Unregister ${id} from cortex-desktop? (Phase 0: registry-only — install_dir is left untouched.)`)) {
      return
    }
    setBusyId(id)
    setActionMessage(null)
    try {
      await apiFetch(`/plugins/${id}`, { method: 'DELETE' })
      setActionMessage(`Uninstalled ${id}`)
      await refresh()
    } catch (e) {
      setActionMessage(
        `Uninstall failed: ${e instanceof Error ? e.message : String(e)}`
      )
    }
    setBusyId(null)
  }

  const handleInstall = async (id: string) => {
    setBusyId(id)
    setActionMessage(
      `Installing ${id}… downloading the bundle from GitHub, ` +
        `verifying checksum, extracting to %APPDATA%\\Cortex\\plugins\\, ` +
        `and starting the sidecar. This usually takes 30-90 seconds.`
    )
    // Use raw fetch — apiFetch's default headers + envelope are fine,
    // but the install can take 60-120s and the default fetch timeout
    // is per-browser; explicit AbortController gives us a known cap.
    const ctrl = new AbortController()
    const timeoutId = window.setTimeout(() => ctrl.abort(), 5 * 60 * 1000)
    try {
      const r = await fetch('/api/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: id }),
        signal: ctrl.signal,
      })
      const text = await r.text()
      if (!r.ok) {
        // Surface the structured detail.message if FastAPI gave us one
        let nice = text
        try {
          const parsed = JSON.parse(text)
          nice =
            (typeof parsed.detail === 'string' && parsed.detail) ||
            (typeof parsed.detail?.message === 'string' && parsed.detail.message) ||
            text
        } catch {
          /* leave nice = text */
        }
        throw new Error(`Install failed (HTTP ${r.status}): ${nice}`)
      }
      setActionMessage(
        `Installed ${id}. Sidecar should flip to Running within ` +
          `5 seconds. If it stays Stopped, check the Plugins logs at ` +
          `%APPDATA%\\Cortex\\plugins\\${id}\\logs\\ — usually a port ` +
          `conflict (something else is using the same port) or a ` +
          `missing system dependency.`
      )
      await refresh()
    } catch (e) {
      const msg =
        e instanceof Error && e.name === 'AbortError'
          ? `Install timed out after 5 minutes. Bundle download might be slow or stalled.`
          : e instanceof Error
            ? e.message
            : String(e)
      setActionMessage(msg)
    } finally {
      window.clearTimeout(timeoutId)
      setBusyId(null)
    }
  }

  const handleDevRegister = async (id: string) => {
    setBusyId(id)
    setActionMessage(null)
    try {
      await apiFetch('/plugins/dev-register', {
        method: 'POST',
        body: JSON.stringify({ plugin_id: id }),
      })
      setActionMessage(
        `Registered ${id} in dev mode. Make sure the sidecar is ` +
          `running on its default port; status will flip to Running ` +
          `within 5 seconds.`
      )
      await refresh()
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : String(e))
    }
    setBusyId(null)
  }

  const handleUpdate = async (id: string) => {
    setBusyId(id)
    setActionMessage(`Updating ${id}… downloading the new bundle, verifying, swapping, restarting. 30-90 seconds.`)
    const ctrl = new AbortController()
    const timeoutId = window.setTimeout(() => ctrl.abort(), 5 * 60 * 1000)
    try {
      const r = await fetch(`/api/plugins/${id}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
      })
      const text = await r.text()
      if (!r.ok) {
        let nice = text
        try {
          const parsed = JSON.parse(text)
          nice =
            (typeof parsed.detail === 'string' && parsed.detail) ||
            (typeof parsed.detail?.message === 'string' && parsed.detail.message) ||
            text
        } catch {
          /* leave nice = text */
        }
        throw new Error(`Update failed (HTTP ${r.status}): ${nice}`)
      }
      setActionMessage(
        `Updated ${id}. The new version should be Running within 5 seconds.`,
      )
      await refresh()
    } catch (e) {
      const msg =
        e instanceof Error && e.name === 'AbortError'
          ? `Update timed out after 5 minutes.`
          : e instanceof Error
            ? e.message
            : String(e)
      setActionMessage(msg)
    } finally {
      window.clearTimeout(timeoutId)
      setBusyId(null)
    }
  }

  const handleCheckUpdates = async () => {
    setCheckingUpdates(true)
    setActionMessage(null)
    try {
      const res = await apiFetch<{
        ok: boolean
        checked_at: string
        result: Record<string, string | null>
      }>('/plugins/check-updates', { method: 'POST' })
      const available = Object.entries(res.result).filter(
        ([, v]) => v !== null,
      )
      if (available.length === 0) {
        setActionMessage(
          `Checked at ${new Date(res.checked_at).toLocaleTimeString()} — all plugins are up to date.`,
        )
      } else {
        setActionMessage(
          `Checked at ${new Date(res.checked_at).toLocaleTimeString()} — updates available: ${available
            .map(([id, v]) => `${id} → v${v}`)
            .join(', ')}`,
        )
      }
      await refresh()
    } catch (e) {
      setActionMessage(
        `Update check failed: ${e instanceof Error ? e.message : String(e)}`,
      )
    } finally {
      setCheckingUpdates(false)
    }
  }

  return (
    <div className="bg-surface rounded-xl p-5 border border-border">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-text-primary">Plugins</h2>
          <p className="text-xs text-text-muted mt-0.5">
            Sidecar services that extend Cortex Hub. Each plugin runs as
            its own process and is proxied through the Hub.
          </p>
        </div>
        {plugins.length > 0 && (
          <button
            onClick={handleCheckUpdates}
            disabled={checkingUpdates}
            title={
              "Hits GitHub for the latest release of each installed plugin. " +
              "Also runs once on Hub startup; this button is a manual refresh."
            }
            className="px-3 py-1.5 text-xs rounded border border-border text-text-secondary hover:bg-surface-tertiary disabled:opacity-50 cursor-pointer whitespace-nowrap shrink-0"
          >
            {checkingUpdates ? 'Checking…' : 'Check for updates'}
          </button>
        )}
      </div>

      {loading && (
        <p className="text-sm text-text-muted">Loading plugins…</p>
      )}
      {error && !loading && (
        <p className="text-sm text-error">Could not load plugins: {error}</p>
      )}

      {/* Installed */}
      {!loading && (
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Installed
          </h3>
          {plugins.length === 0 && (
            <p className="text-sm text-text-muted italic">
              No plugins installed yet.
            </p>
          )}
          {plugins.map((p) => (
            <div key={p.id} className="space-y-2">
              <PluginRow
                plugin={p}
                busy={busyId === p.id}
                configurable={CONFIGURABLE_PLUGINS.has(p.id)}
                isConfiguring={configuringId === p.id}
                onRestart={() => handleRestart(p.id)}
                onUninstall={() => handleUninstall(p.id)}
                onUpdate={() => handleUpdate(p.id)}
                onToggleConfigure={() =>
                  setConfiguringId(configuringId === p.id ? null : p.id)
                }
              />
              {configuringId === p.id && p.id === 'cortex-vision' && (
                <CortexVisionConfigForm
                  onClose={() => setConfiguringId(null)}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Marketplace */}
      {marketplace.length > 0 && (
        <div className="space-y-3 mt-6 pt-6 border-t border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Available
          </h3>
          {marketplace.map((m) => (
            <MarketplaceRow
              key={m.id}
              plugin={m}
              busy={busyId === m.id}
              onInstall={() => handleInstall(m.id)}
              onDevRegister={() => handleDevRegister(m.id)}
            />
          ))}
        </div>
      )}

      {actionMessage && (
        <div className="mt-4 text-xs text-text-secondary bg-surface-tertiary rounded-md p-3 whitespace-pre-wrap">
          {actionMessage}
        </div>
      )}
    </div>
  )
}

function PluginRow({
  plugin,
  busy,
  configurable,
  isConfiguring,
  onRestart,
  onUninstall,
  onUpdate,
  onToggleConfigure,
}: {
  plugin: InstalledPlugin
  busy: boolean
  configurable: boolean
  isConfiguring: boolean
  onRestart: () => void
  onUninstall: () => void
  onUpdate: () => void
  onToggleConfigure: () => void
}) {
  // Dev mode = no managed executable (registered via dev-register or
  // by hand-editing registry.json). The is_dev_mode runtime flag only
  // gets set when start() is called, which never happens for
  // auto_start=false dev entries — so we derive from executable===null.
  const isDevMode = plugin.executable === null
  const dotColor = plugin.is_running ? 'bg-success' : 'bg-error'
  const status = plugin.is_running
    ? 'Running'
    : isDevMode
      ? 'Dev mode (sidecar offline)'
      : 'Stopped'
  const lastCheck = plugin.last_health_check
    ? new Date(plugin.last_health_check).toLocaleTimeString()
    : '—'
  const hasUpdate =
    plugin.latest_available_version != null &&
    plugin.latest_available_version !== plugin.version

  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${dotColor}`} />
            <span className="text-sm font-semibold text-text-primary">
              {plugin.id}
            </span>
            <span className="text-xs text-text-muted">
              v{plugin.version} · {plugin.variant}
            </span>
            {isDevMode && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent-hover">
                DEV
              </span>
            )}
            {hasUpdate && (
              <span
                className="text-[10px] px-1.5 py-0.5 rounded bg-success/15 text-success font-semibold"
                title="A newer version is available on GitHub. Click Update on the row's Restart button menu (TBD) or use the Plugins API."
              >
                ↑ v{plugin.latest_available_version}
              </span>
            )}
          </div>
          <p className="text-xs text-text-muted mt-1">
            {status} · last health: {lastCheck} · {plugin.host}:{plugin.port}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {hasUpdate && !isDevMode && (
            <button
              onClick={onUpdate}
              disabled={busy}
              className="px-2 py-1 text-xs rounded border border-success/50 bg-success/10 text-success hover:bg-success/20 disabled:opacity-50 cursor-pointer"
            >
              Update to v{plugin.latest_available_version}
            </button>
          )}
          {configurable && (
            <button
              onClick={onToggleConfigure}
              disabled={busy}
              className={`px-2 py-1 text-xs rounded border disabled:opacity-50 cursor-pointer ${
                isConfiguring
                  ? 'border-accent/50 bg-accent/10 text-accent-hover'
                  : 'border-border hover:bg-surface-tertiary'
              }`}
            >
              {isConfiguring ? 'Close config' : 'Configure'}
            </button>
          )}
          {!isDevMode && (
            <button
              onClick={onRestart}
              disabled={busy}
              className="px-2 py-1 text-xs rounded border border-border hover:bg-surface-tertiary disabled:opacity-50 cursor-pointer"
            >
              Restart
            </button>
          )}
          <button
            onClick={onUninstall}
            disabled={busy}
            className="px-2 py-1 text-xs rounded border border-border hover:bg-error/10 hover:text-error hover:border-error/40 disabled:opacity-50 cursor-pointer"
          >
            Uninstall
          </button>
        </div>
      </div>
    </div>
  )
}

function MarketplaceRow({
  plugin,
  busy,
  onInstall,
  onDevRegister,
}: {
  plugin: MarketplacePlugin
  busy: boolean
  onInstall: () => void
  onDevRegister: () => void
}) {
  const portLabel = plugin.default_port ? `:${plugin.default_port}` : ''
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-text-primary">
              {plugin.name}
            </span>
            <span className="text-xs text-text-muted">{plugin.id}</span>
          </div>
          <p className="text-xs text-text-secondary mt-1">{plugin.description}</p>
          <a
            href={`https://github.com/${plugin.github_repo}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-accent hover:text-accent-hover"
          >
            github.com/{plugin.github_repo}
          </a>
        </div>
        <div className="shrink-0 flex flex-col items-end gap-1.5">
          {plugin.installed ? (
            <span className="text-xs text-text-muted italic">Installed</span>
          ) : (
            <>
              <button
                onClick={onInstall}
                disabled={busy}
                className="px-3 py-1.5 text-xs rounded bg-accent/15 text-accent-hover hover:bg-accent/25 disabled:opacity-50 cursor-pointer"
              >
                Install
              </button>
              <button
                onClick={onDevRegister}
                disabled={busy}
                title={`Track an externally-running sidecar at 127.0.0.1${portLabel} as a dev plugin. Use this when you're running the plugin from source.`}
                className="px-3 py-1.5 text-xs rounded border border-border text-text-secondary hover:bg-surface-tertiary hover:text-text-primary disabled:opacity-50 cursor-pointer whitespace-nowrap"
              >
                Register dev sidecar
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
