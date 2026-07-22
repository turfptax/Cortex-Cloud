import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../../lib/api'
import { JournalTab } from '../overseer/JournalTab'
import { type JournalResp } from '../overseer/shared'

/** UI redesign Phase 1 (2026-06-10): Journal as a top-level section.
 * Mounts the existing JournalTab (human journal + voice + temporal
 * narratives + overseer reflections) with its own data fetch instead
 * of OverseerPage's state. */

export function JournalPage() {
  const journal = useQuery({
    queryKey: ['overseer-journal'],
    queryFn: () => apiFetch<JournalResp>('/overseer/journal?limit=100'),
    staleTime: 60_000,
  })

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <JournalTab
        overseerEntries={journal.data?.entries ?? []}
        onRefreshOverseerJournal={() => journal.refetch()}
      />
    </div>
  )
}
