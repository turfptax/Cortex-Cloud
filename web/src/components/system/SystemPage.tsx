import { lazy, Suspense, useEffect, useState } from 'react'
import { type StatusInfo } from '../../App'
import { NotesPanel } from './NotesPanel'
import { usePi } from '../../hooks/usePi'
import { useCloudMode } from '../../hooks/useCloudMode'
import { DataPage } from '../data/DataPage'
import { VideoPage } from '../video/VideoPage'
import { LemonSyncPanel } from './LemonSyncPanel'
import { ChatPage } from '../chat/ChatPage'
import { PanelLoading } from '../overseer/panels/widgets'

// Perf (2026-07-11): ActivityPanel pulls the graph stack
// (@xyflow/react + d3-force); lazy-load it with the other graph tabs.
const ActivityPanel = lazy(() =>
  import('../overseer/ActivityPanel').then((m) => ({ default: m.ActivityPanel })))

/** UI redesign Phase 1+2 (2026-06): the ops section. Absorbs the old
 * top-level Pi, Data, and Video tabs plus the overseer's Activity
 * feed as sub-tabs; the pages themselves are mounted unchanged.
 * IA overhaul 2026-07-10: the legacy LM Studio chat demoted here as
 * Local LM (top-level Chat is now the Cortex memory chat). */

type SystemTab = 'notes' | 'data' | 'activity' | 'lemonsync' | 'locallm' | 'video'

export function SystemPage({
  status,
  visionRunning,
}: {
  status: StatusInfo
  visionRunning: boolean
}) {
  const [tab, setTab] = useState<SystemTab>('data')
  const { cloud } = useCloudMode()
  const { notes, fetchNotes, sendNote } = usePi()
  useEffect(() => {
    if (tab === 'notes') fetchNotes()
  }, [tab, fetchNotes])
  // Lemon Sync and Local LM are desktop-only (Lemon egress runs on the
  // workstation; Local LM talks to LM Studio there). Hidden in the cloud
  // Hub, where they have no home. Video already appears only when the
  // local vision sidecar is running, which never happens in the cloud.
  const tabs: { id: SystemTab; label: string }[] = [
    { id: 'notes', label: 'Notes' },
    { id: 'data', label: 'Data' },
    { id: 'activity', label: 'Activity' },
    ...(cloud ? [] : [{ id: 'lemonsync' as SystemTab, label: 'Lemon Sync' }]),
    ...(cloud ? [] : [{ id: 'locallm' as SystemTab, label: 'Local LM' }]),
    ...(visionRunning
      ? [{ id: 'video' as SystemTab, label: 'Video' }]
      : []),
  ]
  // If a hidden tab is somehow active (e.g. a stale deep link), fall
  // back to Data rather than render a desktop-only panel.
  const hidden = (t: SystemTab) =>
    (t === 'video' && !visionRunning) ||
    (cloud && (t === 'lemonsync' || t === 'locallm'))
  const active = hidden(tab) ? 'data' : tab

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex gap-1 px-4 pt-3 border-b border-border shrink-0">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors cursor-pointer ${
              active === t.id
                ? 'bg-surface-secondary text-accent-hover'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        {active === 'notes' && (
          <div className="flex-1 overflow-y-auto p-6">
            <NotesPanel notes={notes} onRefresh={fetchNotes} onSend={sendNote} isOnline={status.coreOnline} />
          </div>
        )}
        {active === 'data' && <DataPage status={status} />}
        {active === 'activity' && (
          <div className="flex-1 overflow-y-auto p-6">
            <Suspense fallback={<PanelLoading label="activity feed" />}>
              <ActivityPanel />
            </Suspense>
          </div>
        )}
        {active === 'lemonsync' && <LemonSyncPanel />}
        {active === 'locallm' && <ChatPage />}
        {active === 'video' && visionRunning && <VideoPage />}
      </div>
    </div>
  )
}
