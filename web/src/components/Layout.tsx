import { type ReactNode, useEffect, useState } from 'react'
import { type Page, type StatusInfo } from '../App'
import { apiFetch } from '../lib/api'

interface LayoutProps {
  page: Page
  setPage: (page: Page) => void
  status: StatusInfo
  children: ReactNode
}

interface NavItem {
  id: Page
  label: string
  icon: string
}

// UI redesign Phase 1 (2026-06-10): sections organized by what
// things ARE, not which agent made them. Video lives under System.
const navItems: NavItem[] = [
  { id: 'search', label: 'Search', icon: '🔍' },
  { id: 'corpus', label: 'Corpus', icon: '🧠' },
  { id: 'chat', label: 'Chat', icon: '💬' },
  { id: 'simples', label: 'Simples', icon: '📅' },
  { id: 'journal', label: 'Journal', icon: '📓' },
  { id: 'system', label: 'System', icon: '🛠️' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
]

export function Layout({ page, setPage, status, children }: LayoutProps) {
  const visibleNav = navItems

  // v0.18.0-dev.25 (2026-05-19): show the running version in the
  // sidebar header so it's always visible. The /version endpoint
  // returns instantly (no GitHub call), so this populates within ms
  // of app mount — no more "0.1.0" stub during the check-update wait.
  const [version, setVersion] = useState<string>('')
  useEffect(() => {
    apiFetch<{ current_version?: string }>('/settings/version')
      .then((r) => setVersion(r.current_version || ''))
      .catch(() => setVersion(''))
  }, [])

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-56 bg-surface-secondary border-r border-border flex flex-col shrink-0">
        {/* Logo */}
        <div className="p-4 border-b border-border">
          <h1 className="text-lg font-bold text-text-primary">Cortex Hub</h1>
          <p className="text-xs text-text-muted mt-0.5">
            Control Center
            {version && (
              <span
                className="ml-1.5 font-mono text-text-muted/70"
                title={`Running cortex-desktop ${version}`}
              >
                v{version}
              </span>
            )}
          </p>
        </div>

        {/* Navigation */}
        <nav className="p-2 space-y-1">
          {visibleNav.map((item) => (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg flex items-center gap-3 transition-colors cursor-pointer ${
                page === item.id
                  ? 'bg-accent/15 text-accent-hover'
                  : 'text-text-secondary hover:bg-surface-tertiary hover:text-text-primary'
              }`}
            >
              <span className="text-lg">{item.icon}</span>
              <span className="font-medium text-sm">{item.label}</span>
            </button>
          ))}
        </nav>

        {/* Scrollable middle section */}
        <div className="flex-1 overflow-y-auto min-h-0" />

        {/* Status indicators */}
        <div className="p-3 border-t border-border space-y-2">
          <StatusDot label="LM Studio" online={status.lmstudioOnline} />
          <StatusDot label="Cortex Cloud" online={status.coreOnline} />
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>
    </div>
  )
}

function StatusDot({ label, online }: { label: string; online: boolean }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <div
        className={`w-2 h-2 rounded-full ${
          online ? 'bg-success' : 'bg-text-muted'
        }`}
      />
      <span className="text-text-muted">{label}</span>
      <span className={online ? 'text-success' : 'text-text-muted'}>
        {online ? 'Online' : 'Offline'}
      </span>
    </div>
  )
}
