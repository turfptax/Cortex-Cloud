import { type ReactNode, useEffect, useState } from 'react'
import { type Page } from '../App'
import { SESSION_EXPIRED_EVENT, SIGN_OUT_URL, signInUrl } from '../lib/api'

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
  // An expired Entra session used to be invisible: the gateway 401s (it
  // deliberately does not redirect, so SPA fetches fail visibly), every page
  // coalesced the failure to empty, and the owner was told his corpus was
  // quiet. One listener, one banner, one way back in.
  const [sessionExpired, setSessionExpired] = useState(false)
  useEffect(() => {
    const onExpired = () => setSessionExpired(true)
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [])

  return (
    <div className="flex h-screen">
      {sessionExpired && (
        <div
          role="alert"
          className="fixed top-0 inset-x-0 z-50 bg-danger/95 text-white
                     px-4 py-2.5 flex items-center justify-center gap-4
                     text-sm shadow-lg"
        >
          <span>Your session expired. Cortex cannot load or save anything until you sign in again.</span>
          <a
            href={signInUrl()}
            className="rounded-md bg-white/15 hover:bg-white/25 px-3 py-1
                       font-medium underline-offset-2"
          >
            Sign in again
          </a>
        </div>
      )}

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

        {/* Sign out. It already existed, buried inside Settings > Cloud
          * settings, which made it effectively undiscoverable and made it
          * impossible to deliberately exercise a signed-out state while
          * testing login behaviour. */}
        <div className="p-2 border-t border-border">
          <a
            href={SIGN_OUT_URL}
            className="w-full text-left px-3 py-2 rounded-lg flex items-center
                       gap-3 text-text-muted hover:bg-surface-tertiary
                       hover:text-text-primary transition-colors"
          >
            <span className="text-lg">🚪</span>
            <span className="font-medium text-sm">Sign out</span>
          </a>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>
    </div>
  )
}
