import { useEffect, useState } from 'react'
import { apiFetch } from '../../lib/api'

type Channel = 'stable' | 'dev'

interface UpdateInfo {
  ok: boolean
  current_version: string
  latest_version: string
  update_available: boolean
  release_url?: string
  download_url?: string
  installer_url?: string
  published_at?: string
  release_notes?: string
  message?: string
  error?: string
  channel?: string
  is_prerelease?: boolean
}

function getStoredChannel(): Channel {
  try {
    const v = localStorage.getItem('cortex-update-channel')
    if (v === 'dev') return 'dev'
  } catch {}
  return 'stable'
}

export function UpdateCard() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [updateError, setUpdateError] = useState('')
  const [updateSuccess, setUpdateSuccess] = useState('')
  const [channel, setChannel] = useState<Channel>(getStoredChannel)
  // v0.18.0-dev.25: fetch the running version on mount so the
  // footer shows it before any /check-update call returns. Previously
  // it fell back to a hardcoded '0.1.0' until the user clicked Check.
  const [currentVersion, setCurrentVersion] = useState<string>('')
  useEffect(() => {
    apiFetch<{ current_version?: string }>('/settings/version')
      .then((r) => setCurrentVersion(r.current_version || ''))
      .catch(() => setCurrentVersion(''))
  }, [])

  const handleChannelChange = (ch: Channel) => {
    setChannel(ch)
    setInfo(null)
    setUpdateError('')
    setUpdateSuccess('')
    try { localStorage.setItem('cortex-update-channel', ch) } catch {}
  }

  const handleCheck = async () => {
    setChecking(true)
    setUpdateError('')
    try {
      const res = await apiFetch<UpdateInfo>(`/settings/check-update?channel=${channel}`)
      setInfo(res)
    } catch {
      setInfo({ ok: false, error: 'Could not reach update server', current_version: '?', latest_version: '?', update_available: false })
    }
    setChecking(false)
  }

  const handleUpdate = async () => {
    setUpdating(true)
    setUpdateError('')
    setUpdateSuccess('')
    try {
      const res = await apiFetch<{ ok: boolean; error?: string; release_url?: string; message?: string; needs_restart?: boolean }>(`/settings/apply-update?channel=${channel}`, {
        method: 'POST',
      })
      if (!res.ok) {
        if (res.release_url) {
          window.open(res.release_url, '_blank')
          setUpdateError('No installer found — opened release page instead.')
        } else {
          setUpdateError(res.error || 'Update failed')
        }
      } else if (res.needs_restart) {
        setUpdateSuccess(res.message || 'Update applied! Restart the app to use the new version.')
        setInfo(prev => prev ? { ...prev, update_available: false } : prev)
      }
    } catch {
      setUpdateError('Failed to start update. Try downloading manually.')
    }
    setUpdating(false)
  }

  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <h2 className="text-lg font-semibold text-text-primary mb-4 flex items-center gap-2">
        <span>🔄</span> Updates
      </h2>

      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={handleCheck}
          disabled={checking || updating}
          className="px-4 py-2 bg-surface-tertiary text-text-primary text-sm rounded-lg hover:bg-accent/15 hover:text-accent transition-colors disabled:opacity-50 cursor-pointer"
        >
          {checking ? 'Checking...' : 'Check for Updates'}
        </button>

        {/* Channel toggle — bordered + clearer contrast so it
            doesn't blend into the card surface. */}
        <div className="flex items-center bg-surface-tertiary rounded-lg p-0.5 text-xs border border-border shrink-0">
          <button
            onClick={() => handleChannelChange('stable')}
            className={`px-3 py-1 rounded-md transition-colors cursor-pointer ${
              channel === 'stable'
                ? 'bg-accent/20 text-accent-hover font-semibold'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            Stable
          </button>
          <button
            onClick={() => handleChannelChange('dev')}
            className={`px-3 py-1 rounded-md transition-colors cursor-pointer ${
              channel === 'dev'
                ? 'bg-amber-500/25 text-amber-300 font-semibold'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            Dev
          </button>
        </div>

        {info && !info.error && !info.update_available && (
          <span className="text-sm text-success">✅ You're up to date!</span>
        )}
      </div>

      {channel === 'dev' && (
        <p className="text-xs text-amber-400/80 mb-3">
          ⚠️ Dev builds may be unstable. Use for testing new features before stable release.
        </p>
      )}

      {/* Update available banner */}
      {info?.update_available && (
        <div className={`${info.is_prerelease ? 'bg-amber-500/10 border-amber-500/30' : 'bg-accent/10 border-accent/30'} border rounded-lg p-4 space-y-3`}>
          <div className="flex items-center justify-between">
            <div>
              <p className={`text-sm font-medium ${info.is_prerelease ? 'text-amber-400' : 'text-accent'}`}>
                {info.is_prerelease && <span className="bg-amber-500/20 text-amber-400 text-[10px] uppercase font-bold px-1.5 py-0.5 rounded mr-2">Pre-release</span>}
                Update available: v{info.latest_version}
              </p>
              <p className="text-xs text-text-muted mt-0.5">
                Current: v{info.current_version}
                {info.published_at && (
                  <> &middot; Released {new Date(info.published_at).toLocaleDateString()}</>
                )}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleUpdate}
                disabled={updating}
                className={`px-3 py-1.5 text-white text-xs rounded-lg transition-colors disabled:opacity-50 cursor-pointer ${
                  info.is_prerelease
                    ? 'bg-amber-600 hover:bg-amber-500'
                    : 'bg-accent hover:bg-accent-hover'
                }`}
              >
                {updating ? 'Installing...' : 'Install Update'}
              </button>
              {info.release_url && (
                <a
                  href={info.release_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-lg hover:bg-surface-secondary transition-colors"
                >
                  Release Notes
                </a>
              )}
            </div>
          </div>

          {updating && (
            <p className={`text-xs animate-pulse ${info.is_prerelease ? 'text-amber-400' : 'text-accent'}`}>
              Downloading update... The app will close and restart automatically.
            </p>
          )}

          {updateError && (
            <p className="text-xs text-red-400">{updateError}</p>
          )}

          {updateSuccess && (
            <p className="text-xs text-success">{updateSuccess}</p>
          )}

          {info.release_notes && (
            <div className="text-xs text-text-secondary bg-surface-secondary rounded-lg p-3 max-h-32 overflow-y-auto whitespace-pre-wrap">
              {info.release_notes}
            </div>
          )}
        </div>
      )}

      {/* No releases yet */}
      {info && !info.error && !info.update_available && info.message && (
        <p className="text-xs text-text-muted">{info.message}</p>
      )}

      {/* Error */}
      {info?.error && (
        <p className="text-xs text-red-400">Failed to check: {info.error}</p>
      )}

      {/* Version footer */}
      <p className="text-xs text-text-muted mt-3">
        Current version: v{info?.current_version || currentVersion || '…'}
        {channel === 'dev' && <span className="text-amber-400/60"> &middot; Dev channel</span>}
      </p>
    </div>
  )
}
