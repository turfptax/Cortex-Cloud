import { useState, useEffect, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Layout } from './components/Layout'
import { SearchPage } from './components/search/SearchPage'
import { SettingsPage } from './components/settings/SettingsPage'
import { OverseerPage } from './components/overseer/OverseerPage'
import { SimplesPage } from './components/simples/SimplesPage'
import { JournalPage } from './components/journal/JournalPage'
import { SystemPage } from './components/system/SystemPage'
import { apiFetch } from './lib/api'
import { useInstalledPlugins } from './hooks/useInstalledPlugins'

export type Page =
  | 'search' | 'corpus' | 'chat' | 'simples' | 'journal' | 'system'
  | 'settings'

const PAGES: readonly Page[] = [
  'search', 'corpus', 'chat', 'simples', 'journal', 'system', 'settings',
]

// Pre-redesign hashes keep working (bookmarks, muscle memory).
const LEGACY_ALIASES: Record<string, Page> = {
  overseer: 'corpus',
  pi: 'system',
  data: 'system',
  video: 'system',
}

export interface StatusInfo {
  lmstudioOnline: boolean
  coreOnline: boolean
}

/** The URL hash is the source of truth for the top-level tab, so
 * tabs survive a refresh and can be deep-linked / bookmarked
 * (e.g. http://localhost:8003/#/search). */
function pageFromHash(): Page {
  // First segment is the page; sections own deeper segments
  // (e.g. #/corpus/insights -> page 'corpus', sub-tab 'insights').
  const h = window.location.hash.replace(/^#\/?/, '').split('/')[0]
  if ((PAGES as readonly string[]).includes(h)) return h as Page
  if (h in LEGACY_ALIASES) return LEGACY_ALIASES[h]
  return 'search'
}

function useHashPage(): [Page, (p: Page) => void] {
  const [page, setPageState] = useState<Page>(pageFromHash)

  useEffect(() => {
    const onHashChange = () => setPageState(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const setPage = useCallback((p: Page) => {
    window.location.hash = `/${p}`
  }, [])

  return [page, setPage]
}

interface HealthSnapshot {
  status: StatusInfo
}

async function fetchHealthSnapshot(): Promise<HealthSnapshot> {
  const [chatHealth, piHealth] = await Promise.allSettled([
    apiFetch<{ lmstudio_online: boolean }>('/chat/health'),
    apiFetch<{ online: boolean }>('/pi/online'),
  ])

  return {
    status: {
      lmstudioOnline:
        chatHealth.status === 'fulfilled'
          ? chatHealth.value.lmstudio_online
          : false,
      coreOnline:
        piHealth.status === 'fulfilled' ? piHealth.value.online : false,
    },
  }
}

function App() {
  const [page, setPage] = useHashPage()

  // Connectivity polling through the shared query cache (was a
  // hand-rolled setInterval). refetchInterval keeps the 15s cadence;
  // any other component can read the same snapshot via its queryKey
  // without firing a second request.
  const { data: health } = useQuery({
    queryKey: ['health-snapshot'],
    queryFn: fetchHealthSnapshot,
    refetchInterval: 15_000,
  })
  const status = health?.status ?? { lmstudioOnline: false, coreOnline: false }

  const { plugins } = useInstalledPlugins()
  const visionRunning =
    plugins.find((p) => p.id === 'cortex-vision')?.is_running ?? false

  return (
    <Layout page={page} setPage={setPage} status={status}>
      {page === 'search' && <SearchPage />}
      {/* Chat is the overseer chat promoted to a top-level surface
        * (IA overhaul 2026-07-10); it shares the OverseerPage instance
        * so switching corpus <-> chat never drops composer state. The
        * legacy LM Studio chat lives under System > Local LM. */}
      {(page === 'corpus' || page === 'chat') && <OverseerPage />}
      {page === 'simples' && <SimplesPage />}
      {page === 'journal' && <JournalPage />}
      {page === 'system' && (
        <SystemPage status={status} visionRunning={visionRunning} />
      )}
      {page === 'settings' && <SettingsPage />}
    </Layout>
  )
}

export default App
