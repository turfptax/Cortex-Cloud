import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '../lib/api'

/** One row from /api/plugins. Matches InstalledPlugin.to_api_dict()
 * on the backend. Runtime fields (is_running, is_dev_mode) are
 * authoritative — registry fields are persisted. */
export interface InstalledPlugin {
  id: string
  version: string
  variant: string
  install_dir: string | null
  executable: string | null
  host: string
  port: number
  auto_start: boolean
  installed_at: string
  last_health_check: string | null
  /** Auto-respawn on crash. Default true for installed plugins;
   * irrelevant for dev-mode entries. */
  restart_on_crash?: boolean
  /** Cached from the periodic + manual update checks. Non-null
   * means an upgrade is available to that version. */
  latest_available_version?: string | null
  last_update_check_at?: string | null
  is_running: boolean
  is_dev_mode: boolean
}

export interface MarketplacePlugin {
  id: string
  name: string
  description: string
  github_repo: string
  manifest_url: string
  default_port?: number
  installed: boolean
}

const REFRESH_INTERVAL_MS = 5_000

/** Poll /api/plugins every 5s. Used by Layout (to gate the Video nav
 * item) and by PluginsTab (to render status). */
export function useInstalledPlugins() {
  const [plugins, setPlugins] = useState<InstalledPlugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const list = await apiFetch<InstalledPlugin[]>('/plugins')
      setPlugins(list)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [refresh])

  return { plugins, loading, error, refresh }
}

export function isPluginRunning(
  plugins: InstalledPlugin[],
  pluginId: string,
): boolean {
  const p = plugins.find((x) => x.id === pluginId)
  return Boolean(p && p.is_running)
}
