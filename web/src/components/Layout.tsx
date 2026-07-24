import { type ReactNode } from 'react'
import { type Page } from '../App'

interface LayoutProps {
  page: Page
  setPage: (page: Page) => void
  children: ReactNode
}

interface NavItem {
  id: Page
  label: string
  icon: string
}

// Cloud web nav. The desktop-era System tab (Pi/Data/Video/Local-LM) and
// the LM Studio / Pi status dots are gone; this is a cloud-only, single-
// owner app. Tab structure moves to the phone-mirror IA in a later slice.
const navItems: NavItem[] = [
  { id: 'today', label: 'Today', icon: '🏠' },
  { id: 'search', label: 'Search', icon: '🔍' },
  { id: 'corpus', label: 'Corpus', icon: '🧠' },
  { id: 'chat', label: 'Chat', icon: '💬' },
  { id: 'simples', label: 'Plan', icon: '📅' },
  { id: 'journal', label: 'Journal', icon: '📓' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
]

export function Layout({ page, setPage, children }: LayoutProps) {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-56 bg-surface-secondary border-r border-border flex flex-col shrink-0">
        {/* Logo */}
        <div className="p-4 border-b border-border">
          <h1 className="text-lg font-bold text-text-primary">Cortex</h1>
          <p className="text-xs text-text-muted mt-0.5">Your memory, in the cloud</p>
        </div>

        {/* Navigation */}
        <nav className="p-2 space-y-1">
          {navItems.map((item) => (
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
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>
    </div>
  )
}
