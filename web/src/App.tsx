import { useState, useEffect, useCallback } from 'react'
import { Layout } from './components/Layout'
import { SearchPage } from './components/search/SearchPage'
import { SettingsPage } from './components/settings/SettingsPage'
import { OverseerPage } from './components/overseer/OverseerPage'
import { SimplesPage } from './components/simples/SimplesPage'
import { JournalPage } from './components/journal/JournalPage'

export type Page =
  | 'search' | 'corpus' | 'chat' | 'simples' | 'journal' | 'settings'

const PAGES: readonly Page[] = [
  'search', 'corpus', 'chat', 'simples', 'journal', 'settings',
]

// Pre-redesign hashes keep working (bookmarks, muscle memory). The old
// desktop-era System tab (Pi/Data/Video/Local-LM) is gone in the cloud.
const LEGACY_ALIASES: Record<string, Page> = {
  overseer: 'corpus',
  system: 'corpus',
  pi: 'corpus',
  data: 'corpus',
  video: 'corpus',
}

/** The URL hash is the source of truth for the top-level tab, so
 * tabs survive a refresh and can be deep-linked / bookmarked
 * (e.g. https://cortex.turfptax.com/#/search). */
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

function App() {
  const [page, setPage] = useHashPage()

  return (
    <Layout page={page} setPage={setPage}>
      {page === 'search' && <SearchPage />}
      {/* Chat is the overseer chat promoted to a top-level surface
        * (IA overhaul 2026-07-10); it shares the OverseerPage instance
        * so switching corpus <-> chat never drops composer state. */}
      {(page === 'corpus' || page === 'chat') && <OverseerPage />}
      {page === 'simples' && <SimplesPage />}
      {page === 'journal' && <JournalPage />}
      {page === 'settings' && <SettingsPage />}
    </Layout>
  )
}

export default App
