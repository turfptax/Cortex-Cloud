import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../../lib/api'
import type { GraphResp } from './ExplorerPanel'
import { ProjectsTab } from './ProjectsTab'

// Perf (2026-07-11): the graph tabs pull @xyflow/react + d3-force,
// the heaviest deps in the app. Lazy-load them so the initial bundle
// (Chat/Search/Corpus overview) ships without the graph stack.
const ExplorerPanel = lazy(() =>
  import('./ExplorerPanel').then((m) => ({ default: m.ExplorerPanel })))
const EcosystemMapPanel = lazy(() =>
  import('./EcosystemMapPanel').then((m) => ({ default: m.EcosystemMapPanel })))
import { useVoiceMode } from '../../hooks/useVoiceMode'
import { useCloudMode } from '../../hooks/useCloudMode'
import { NotificationsPanel } from './panels/NotificationsPanel'
import { ChatPanel } from './panels/ChatPanel'
import { InsightsPanel } from './panels/InsightsPanel'
import { WorkingMemoryView } from './panels/WorkingMemoryView'
import { Card, PanelLoading, SourceBadge } from './panels/widgets'
import { StatCard, Row } from './panels/WorkingMemoryView'
import { SqueezePanel } from './panels/SqueezePanel'
import { ContactsPanel } from './panels/ContactsPanel'
import { VoicePanel } from './panels/VoicePanel'

import {
  type StatusResp,
  type WorkingMemoryResp,
  type ImportRow,
  type ImportsResp,
  type ScanRow,
  type ScanResp,
  type LoopResp,
  type LlmStatsResp,
  type ChatAttachmentRef,
  type ChatToolCallSummary,
  type ChatMessage,
  type ChatHistoryResp,
  type ChatSendResp,
  type ChatUploadResp,
  type ChatThread,
  type ChatThreadsResp,
  type ChatPrompt,
  type PendingAttachment,
  CHAT_MAX_FILES,
  CHAT_MAX_FILE_BYTES,
  CHAT_ALLOWED_EXTS,
  fileExt,
  classifyKind,
  formatBytes,
  type NotificationRow,
  type NotificationsResp,
  type BudgetSnapshot,
  type BudgetResp,
  type BlindspotRow,
  type PendingInterpretation,
  type InsightScanRow,
  type InsightScansResp,
  type InsightPendingResp,
  type InsightScanResp,
  fmtBytes,
  fmtDuration,
  fmtRelative,
} from './shared'
// ── Page ──────────────────────────────────────────────────────

// UI redesign Phase 2 (2026-06-11): journal moved to the top-level
// Journal section; activity moved to System. Sub-tab is hash-driven
// (#/corpus/<tab>) so corpus views deep-link and survive refresh.
// IA overhaul 2026-07-10: chat promoted to the top-level #/chat surface
// (still this component, so composer state survives section switches);
// Dialectic sunset (writer off since 2026-05-24) and replaced by
// Squeeze (AI report card from graded dispatches); Classify folded
// into the Projects tab.
type Tab = 'overview' | 'chat' | 'squeeze' | 'insights' | 'projects' | 'notifications' | 'explorer' | 'ecosystem' | 'contacts' | 'voice'

const CORPUS_TABS: readonly Tab[] = [
  'overview', 'insights', 'projects', 'squeeze',
  'ecosystem', 'explorer', 'notifications', 'contacts', 'voice',
]

// Old bookmarks keep landing somewhere sensible.
const LEGACY_TAB_ALIASES: Record<string, Tab> = {
  chat: 'chat',
  dialectic: 'squeeze',
  classify: 'projects',
}

function tabFromHash(): Tab {
  const seg = window.location.hash.replace(/^#\/?/, '').split('/')
  if (seg[0] === 'chat') return 'chat'
  const sub = seg[1] ?? ''
  if ((CORPUS_TABS as readonly string[]).includes(sub)) return sub as Tab
  if (sub in LEGACY_TAB_ALIASES) return LEGACY_TAB_ALIASES[sub]
  return 'overview'
}

export function OverseerPage() {
  const [tab, setTabState] = useState<Tab>(tabFromHash)
  useEffect(() => {
    const onHash = () => setTabState(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const setTab = (t: Tab) => {
    window.location.hash = t === 'chat' ? '/chat' : `/corpus/${t}`
  }
  const [status, setStatus] = useState<StatusResp | null>(null)
  const [wm, setWm] = useState<WorkingMemoryResp | null>(null)
  const [imports, setImports] = useState<ImportRow[]>([])
  const [loop, setLoop] = useState<LoopResp | null>(null)
  const [llmStats, setLlmStats] = useState<LlmStatsResp | null>(null)
  const [scan, setScan] = useState<ScanResp | null>(null)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set())
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState<string>('')
  const [chatSending, setChatSending] = useState<boolean>(false)
  // Agent harness (2026-07-10): chat threads + prompt library. The
  // Pi owns the active-thread pointer; the sidebar switches it via
  // /chat/threads/select before reloading history. Sends always go
  // to the active thread, which keeps voice mode + MCP chat coherent.
  const [chatThreads, setChatThreads] = useState<ChatThread[]>([])
  const [activeThreadId, setActiveThreadId] = useState<number>(0)
  const [chatPrompts, setChatPrompts] = useState<ChatPrompt[]>([])
  // Slice 8: pending file attachments queued in the composer
  const [chatPending, setChatPending] = useState<PendingAttachment[]>([])
  // Slice 14.7 CP4: direct-overseer mode. When false (default), the
  // composer posts to /api/overseer/quick-chat — the Flash router
  // handles routine turns and escalates to overseer when needed.
  // When true, posts go straight to /api/overseer/chat (full Opus).
  // Persisted to localStorage so the user's preference survives
  // reloads.
  const [directMode, setDirectMode] = useState<boolean>(() => {
    try {
      return localStorage.getItem('cortex.overseer.directMode') === '1'
    } catch {
      return false
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(
        'cortex.overseer.directMode', directMode ? '1' : '0')
    } catch {
      /* noop */
    }
  }, [directMode])
  const [notifications, setNotifications] = useState<NotificationRow[]>([])
  const [notificationsUnread, setNotificationsUnread] = useState<number>(0)
  const [budget, setBudget] = useState<BudgetSnapshot | null>(null)
  const [blindspots, setBlindspots] = useState<BlindspotRow[]>([])
  const [expandedToken, setExpandedToken] = useState<string | null>(null)
  // Slice 3h: insight queue state
  const [insights, setInsights] = useState<PendingInterpretation[]>([])
  const [insightCounts, setInsightCounts] = useState<InsightPendingResp['counts']>(undefined)
  const [insightScanProject, setInsightScanProject] = useState<string>('')
  const [insightScanDays, setInsightScanDays] = useState<number>(7)
  const [insightStatusFilter, setInsightStatusFilter] = useState<string>('pending')
  const [editingInsightId, setEditingInsightId] = useState<number | null>(null)
  const [editTitle, setEditTitle] = useState<string>('')
  const [editBody, setEditBody] = useState<string>('')
  const [insightScansHistory, setInsightScansHistory] = useState<InsightScanRow[]>([])
  // Polish CP1: Explorer tab state
  const [graph, setGraph] = useState<GraphResp | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState<string>('')
  // Polish CP3: per-project group expand state for the Imports panel
  const [openImportGroups, setOpenImportGroups] = useState<Set<string>>(new Set())
  const toggleImportGroup = (folder: string) => {
    setOpenImportGroups((prev) => {
      const next = new Set(prev)
      if (next.has(folder)) next.delete(folder)
      else next.add(folder)
      return next
    })
  }
  const [busy, setBusy] = useState<string>('')
  const [lastAction, setLastAction] = useState<string>('')
  const [error, setError] = useState<string>('')

  const refreshAll = async () => {
    setError('')
    try {
      const [s, w, l, ls, im, n, b, bs] = await Promise.all([
        apiFetch<StatusResp>('/overseer/status'),
        apiFetch<WorkingMemoryResp>('/overseer/working-memory'),
        apiFetch<LoopResp>('/overseer/loop'),
        apiFetch<LlmStatsResp>('/overseer/llm/stats?days=7'),
        apiFetch<ImportsResp>('/overseer/imports?limit=200'),
        apiFetch<NotificationsResp>('/overseer/notifications'),
        apiFetch<BudgetResp>('/overseer/budget'),
        apiFetch<{ ok: boolean; blindspots?: BlindspotRow[] }>(
          '/overseer/blindspots?active_only=1'
        ),
      ])
      setStatus(s)
      setWm(w)
      setLoop(l)
      setLlmStats(ls)
      setImports(im.imports || [])
      // Slice 9.6 CP1: parse actions_json string into structured
      // actions array so the render layer doesn't reparse per render.
      const parsedNotifs = (n.notifications || []).map((row) => {
        const raw = (row as any).actions_json
        if (typeof raw === 'string' && raw && raw !== '[]') {
          try {
            const parsed = JSON.parse(raw)
            if (Array.isArray(parsed)) return { ...row, actions: parsed }
          } catch {
            // bad json, fall through with empty actions
          }
        }
        return row
      })
      setNotifications(parsedNotifs)
      setNotificationsUnread(n.unread_count || 0)
      setBudget(b.budget || null)
      setBlindspots(bs.blindspots || [])
    } catch (e: any) {
      setError(`Refresh failed: ${e?.message || e}`)
    }
  }

  const refreshChat = async () => {
    try {
      const r = await apiFetch<ChatHistoryResp & { messages?: (ChatMessage & { metadata_json?: string })[] }>(
        '/overseer/chat/history?limit=200',
      )
      // Slice 9.5 CP1: parse metadata_json once so the render layer
      // doesn't reparse on every render and so we get typed fields.
      const parsed: ChatMessage[] = (r.messages || []).map((m) => {
        const out: ChatMessage = { ...m }
        const raw = (m as any).metadata_json
        if (typeof raw === 'string' && raw && raw !== '{}') {
          try {
            const meta = JSON.parse(raw)
            out.raw_metadata = meta
            if (Array.isArray(meta.tool_calls)) {
              out.tool_calls = meta.tool_calls as ChatToolCallSummary[]
            }
            if (typeof meta.tool_iterations === 'number') {
              out.tool_iterations = meta.tool_iterations
            }
            if (typeof meta.history_turns_used === 'number') {
              out.history_turns_used = meta.history_turns_used
            }
            if (typeof meta.context_chars === 'number') {
              out.context_chars = meta.context_chars
            }
          } catch {
            // Bad JSON — leave fields unset, don't crash the render.
          }
        }
        return out
      })
      setChatMessages(parsed)
      if (typeof r.active_thread_id === 'number') {
        setActiveThreadId(r.active_thread_id)
      }
    } catch (e: any) {
      setError(`Chat refresh failed: ${e?.message || e}`)
    }
  }

  // Agent harness (2026-07-10): thread sidebar + prompt library data.
  // Threads refresh alongside chat history (titles/counts move on
  // every send); prompts only on tab open + after CRUD.
  const refreshThreads = async () => {
    try {
      const r = await apiFetch<ChatThreadsResp>('/overseer/chat/threads')
      setChatThreads(r.threads || [])
      if (typeof r.active_thread_id === 'number') {
        setActiveThreadId(r.active_thread_id)
      }
    } catch (e: any) {
      setError(`Threads refresh failed: ${e?.message || e}`)
    }
  }

  const refreshPrompts = async () => {
    try {
      const r = await apiFetch<{ ok: boolean; prompts?: ChatPrompt[] }>(
        '/overseer/chat/prompts',
      )
      setChatPrompts(r.prompts || [])
    } catch (e: any) {
      setError(`Prompts refresh failed: ${e?.message || e}`)
    }
  }

  // Thread mutations reuse chatSending as a global in-flight guard:
  // the sidebar rows + New chat disable while true, which closes the
  // double-click-new and switch-mid-operation races in this window.
  // (Cross-window/cross-surface races are handled server-side by
  // thread pinning.)
  const handleNewThread = async () => {
    if (chatSending) return
    setChatSending(true)
    try {
      const r = await apiFetch<{ ok: boolean; error?: string }>(
        '/overseer/chat/threads/new', {
          method: 'POST', body: JSON.stringify({}),
        })
      if (!r.ok) {
        setError(`New thread failed: ${r.error || 'unknown'}`)
        return
      }
      setChatMessages([])
      await Promise.all([refreshChat(), refreshThreads()])
    } catch (e: any) {
      setError(`New thread failed: ${e?.message || e}`)
    } finally {
      setChatSending(false)
    }
  }

  const handleSelectThread = async (id: number) => {
    if (id === activeThreadId || chatSending) return
    setChatSending(true)
    try {
      const r = await apiFetch<{ ok: boolean; error?: string }>(
        '/overseer/chat/threads/select', {
          method: 'POST', body: JSON.stringify({ thread_id: id }),
        })
      if (!r.ok) {
        setError(`Switch thread failed: ${r.error || 'unknown'}`)
        await refreshThreads()
        return
      }
      setActiveThreadId(id)
      // Blank immediately so the old thread's messages never render
      // under the new thread's header while history loads.
      setChatMessages([])
      await refreshChat()
    } catch (e: any) {
      setError(`Switch thread failed: ${e?.message || e}`)
    } finally {
      setChatSending(false)
    }
  }

  const handleRenameThread = async (id: number, title: string) => {
    try {
      const r = await apiFetch<{ ok: boolean; error?: string }>(
        '/overseer/chat/threads/rename', {
          method: 'POST', body: JSON.stringify({ thread_id: id, title }),
        })
      if (!r.ok) setError(`Rename thread failed: ${r.error || 'unknown'}`)
      await refreshThreads()
    } catch (e: any) {
      setError(`Rename thread failed: ${e?.message || e}`)
    }
  }

  const handleDeleteThread = async (id: number) => {
    if (chatSending) return
    setChatSending(true)
    try {
      const r = await apiFetch<{ ok: boolean; error?: string }>(
        '/overseer/chat/threads/delete', {
          method: 'POST', body: JSON.stringify({ thread_id: id }),
        })
      if (!r.ok) setError(`Delete thread failed: ${r.error || 'unknown'}`)
      // The Pi heals the active pointer; reload both.
      setChatMessages([])
      await Promise.all([refreshChat(), refreshThreads()])
    } catch (e: any) {
      setError(`Delete thread failed: ${e?.message || e}`)
    } finally {
      setChatSending(false)
    }
  }

  const handleSavePrompt = async (title: string, body: string) => {
    try {
      const r = await apiFetch<{ ok: boolean; error?: string }>(
        '/overseer/chat/prompts/upsert', {
          method: 'POST', body: JSON.stringify({ title, body }),
        })
      if (!r.ok) setError(`Save prompt failed: ${r.error || 'unknown'}`)
      await refreshPrompts()
    } catch (e: any) {
      setError(`Save prompt failed: ${e?.message || e}`)
    }
  }

  const handleDeletePrompt = async (id: number) => {
    try {
      const r = await apiFetch<{ ok: boolean; error?: string }>(
        '/overseer/chat/prompts/delete', {
          method: 'POST', body: JSON.stringify({ id }),
        })
      if (!r.ok) setError(`Delete prompt failed: ${r.error || 'unknown'}`)
      await refreshPrompts()
    } catch (e: any) {
      setError(`Delete prompt failed: ${e?.message || e}`)
    }
  }

  // Agent harness (2026-07-11): per-turn meta-feedback. Note-first;
  // the context object is the injection payload the Pi seeds into a
  // Discuss thread, so it must describe what the user was looking at.
  const handleSendFeedback = async (
    targetId: number, rating: number, note: string,
  ): Promise<number | null> => {
    try {
      const thread = chatThreads.find((t) => t.id === activeThreadId)
      const r = await apiFetch<{ ok: boolean; id?: number; error?: string }>(
        '/overseer/feedback', {
          method: 'POST',
          body: JSON.stringify({
            target_kind: 'chat_turn',
            target_id: String(targetId),
            rating,
            note,
            source: 'hub',
            context: {
              screen: 'hub-chat',
              thread_id: activeThreadId,
              thread_title: thread?.title || '',
              direct_mode: directMode,
            },
          }),
        })
      if (!r.ok || !r.id) {
        setError(`Feedback failed: ${r.error || 'unknown'}`)
        return null
      }
      setLastAction('Feedback noted')
      return r.id
    } catch (e: any) {
      setError(`Feedback failed: ${e?.message || e}`)
      return null
    }
  }

  const handleDiscussFeedback = async (feedbackId: number) => {
    // Shares the chatSending lock: discussing switches the active
    // thread, which must not happen mid-send, and the lock also
    // swallows double-clicks (the Pi is idempotent, but two racing
    // requests would still churn).
    if (chatSending) return
    setChatSending(true)
    try {
      const r = await apiFetch<{
        ok: boolean; thread_id?: number; error?: string
      }>('/overseer/feedback/discuss', {
        method: 'POST',
        body: JSON.stringify({ feedback_id: feedbackId }),
      })
      if (!r.ok) {
        setError(`Discuss failed: ${r.error || 'unknown'}`)
        return
      }
      // The Pi created (or re-opened) the seeded thread and made it
      // active; load it.
      setChatMessages([])
      window.location.hash = '/chat'
      await Promise.all([refreshChat(), refreshThreads()])
    } catch (e: any) {
      setError(`Discuss failed: ${e?.message || e}`)
    } finally {
      setChatSending(false)
    }
  }

  const refreshInsights = async (status: string = insightStatusFilter) => {
    try {
      const q = status ? `?status=${encodeURIComponent(status)}` : ''
      const [pending, scans] = await Promise.all([
        apiFetch<InsightPendingResp>(`/overseer/insight/pending${q}`),
        apiFetch<InsightScansResp>(`/overseer/insight/scans?limit=15`),
      ])
      setInsights(pending.interpretations || [])
      setInsightCounts(pending.counts)
      setInsightScansHistory(scans.scans || [])
    } catch (e: any) {
      setError(`Insights refresh failed: ${e?.message || e}`)
    }
  }

  const handleInsightScanNow = async () => {
    if (!insightScanProject.trim()) {
      setError('Pick a project tag to scan (e.g. UFOSINT)')
      return
    }
    setBusy(`Scanning ${insightScanProject}…`)
    setError('')
    setLastAction('')
    try {
      const r = await apiFetch<InsightScanResp>('/overseer/insight/scan-now', {
        method: 'POST',
        body: JSON.stringify({
          project: insightScanProject.trim(),
          days: insightScanDays,
        }),
      })
      if (!r.ok) {
        setError(`Scan failed: ${r.error || 'unknown'}`)
      } else if ((r.candidates_proposed || 0) === 0) {
        setLastAction(
          `Scan complete: ${r.gists_seen} gists seen, no new candidates ` +
          `(deduped: ${r.candidates_deduped ?? 0}, cost $${(r.cost_usd ?? 0).toFixed(4)}).` +
          (r.note ? ` ${r.note}` : ''),
        )
      } else {
        setLastAction(
          `Scan proposed ${r.candidates_proposed} candidates ` +
          `(${r.gists_seen} gists, $${(r.cost_usd ?? 0).toFixed(4)}).`,
        )
      }
      await refreshInsights('pending')
    } catch (e: any) {
      setError(`Scan failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleDistillCorrections = async () => {
    setBusy('Distilling corrections…')
    setError('')
    setLastAction('')
    try {
      const r = await apiFetch<{
        ok: boolean
        corrections_seen?: number
        candidates_proposed?: number
        candidates_deduped?: number
        cost_usd?: number
        note?: string
        error?: string
      }>('/overseer/insight/distill-corrections', {
        method: 'POST',
      })
      if (!r.ok) {
        setError(`Distill failed: ${r.error || 'unknown'}`)
      } else if ((r.candidates_proposed || 0) === 0) {
        setLastAction(
          `Distill: ${r.corrections_seen ?? 0} corrections seen, no new ` +
          `blindspot candidates (deduped: ${r.candidates_deduped ?? 0}, ` +
          `cost $${(r.cost_usd ?? 0).toFixed(4)})` +
          (r.note ? `. ${r.note}` : '.'),
        )
      } else {
        setLastAction(
          `Distill proposed ${r.candidates_proposed} blindspot candidate(s) ` +
          `from ${r.corrections_seen} corrections ($${(r.cost_usd ?? 0).toFixed(4)}).`,
        )
      }
      await refreshInsights('pending')
    } catch (e: any) {
      setError(`Distill failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleInsightDecide = async (
    id: number,
    decision: 'confirm' | 'reject' | 'edit-and-confirm',
    overrides: { edit_title?: string; edit_body?: string; review_note?: string } = {},
  ) => {
    setBusy(`Applying decision…`)
    setError('')
    try {
      const r = await apiFetch<{
        ok: boolean
        status?: string
        applied_table?: string
        applied_id?: number
        error?: string
      }>('/overseer/insight/decide', {
        method: 'POST',
        body: JSON.stringify({ id, decision, ...overrides }),
      })
      if (!r.ok) {
        setError(`Decide failed: ${r.error || 'unknown'}`)
      } else if (decision === 'reject') {
        setLastAction(`Rejected #${id}.`)
      } else if (r.applied_table) {
        setLastAction(
          `Confirmed #${id} → landed in ${r.applied_table}#${r.applied_id}.`,
        )
      } else {
        setLastAction(`#${id} → ${r.status}`)
      }
      setEditingInsightId(null)
      await refreshInsights(insightStatusFilter)
      // The working memory now contains a new pattern/drift/theme,
      // so refresh that too so it shows up in the Overview.
      try {
        const wmResp = await apiFetch<WorkingMemoryResp>('/overseer/working-memory')
        setWm(wmResp)
      } catch {}
    } catch (e: any) {
      setError(`Decide failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  useEffect(() => {
    refreshAll()
    // Perf (2026-07-11): refreshAll is 8 Pi-proxied calls. Skip the
    // 30s tick while the window is hidden (tray-minimized app,
    // background tab) and catch up immediately on return.
    const t = setInterval(() => {
      if (!document.hidden) refreshAll()
    }, 30000)
    const onVisible = () => {
      if (!document.hidden) refreshAll()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(t)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  useEffect(() => {
    if (tab === 'chat') {
      refreshChat()
      refreshThreads()
      refreshPrompts()
    }
    if (tab === 'insights') refreshInsights(insightStatusFilter)
    if (tab === 'explorer' && !graph) refreshGraph()
  }, [tab])

  // Slice 8: validate one File against the local mirror of the Hub's
  // allowlist. Returns an error string on rejection or null if OK.
  const validatePending = (f: File): string | null => {
    const ext = fileExt(f.name)
    if (!CHAT_ALLOWED_EXTS.has(ext)) {
      return `unsupported file type (${ext || 'no extension'})`
    }
    if (f.size === 0) return 'empty file'
    if (f.size > CHAT_MAX_FILE_BYTES) {
      return `too large (${formatBytes(f.size)} > ${formatBytes(CHAT_MAX_FILE_BYTES)})`
    }
    return null
  }

  // Slice 8: enqueue dropped/picked files. Filters out anything that
  // would push past CHAT_MAX_FILES — the rejected files become inline
  // error messages so the user knows why some didn't take.
  const handleAddPendingFiles = (files: FileList | File[]) => {
    const list = Array.from(files)
    if (!list.length) return
    setError('')
    setChatPending((prev) => {
      const room = CHAT_MAX_FILES - prev.length
      if (room <= 0) {
        setError(`Already at max ${CHAT_MAX_FILES} attachments — remove one to add more.`)
        return prev
      }
      const incoming: PendingAttachment[] = []
      for (const f of list.slice(0, room)) {
        const err = validatePending(f)
        const kind = classifyKind(f.name, f.type)
        const previewUrl = (kind === 'image' && !err) ? URL.createObjectURL(f) : undefined
        incoming.push({
          localId: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          file: f,
          filename: f.name,
          size: f.size,
          mime_type: f.type || '',
          status: err ? 'error' : 'queued',
          previewUrl,
          error: err || undefined,
        })
      }
      if (list.length > room) {
        setError(`Only added ${room} files — ${list.length - room} skipped because the cap is ${CHAT_MAX_FILES}.`)
      }
      return [...prev, ...incoming]
    })
  }

  const handleRemovePending = (localId: string) => {
    setChatPending((prev) => {
      const target = prev.find((p) => p.localId === localId)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      return prev.filter((p) => p.localId !== localId)
    })
  }

  // Upload the queued (and not-yet-uploaded, not-error) attachments
  // to the Hub via multipart. Returns the list of refs that succeeded
  // and mutates pending state to reflect upload status. Anything that
  // came back rejected from the Hub is marked with an error so the
  // user can see why it didn't ship.
  const uploadPendingAttachments = async (): Promise<ChatAttachmentRef[]> => {
    const toUpload = chatPending.filter(
      (p) => p.status === 'queued' && !p.error,
    )
    const ready = chatPending.filter((p) => p.status === 'ready' && p.ref)
    if (toUpload.length === 0) {
      return ready.map((p) => p.ref!).filter(Boolean)
    }
    // Mark uploading
    setChatPending((prev) =>
      prev.map((p) => (toUpload.find((t) => t.localId === p.localId)
        ? { ...p, status: 'uploading' as const } : p)),
    )
    const fd = new FormData()
    for (const p of toUpload) fd.append('files', p.file, p.filename)
    let resp: Response
    try {
      resp = await fetch('/api/overseer/chat/upload', {
        method: 'POST', body: fd,
      })
    } catch (e: any) {
      setChatPending((prev) =>
        prev.map((p) => (toUpload.find((t) => t.localId === p.localId)
          ? { ...p, status: 'error' as const, error: 'network: ' + (e?.message || e) }
          : p)),
      )
      throw new Error('Upload network failure')
    }
    if (!resp.ok) {
      const body = await resp.text()
      setChatPending((prev) =>
        prev.map((p) => (toUpload.find((t) => t.localId === p.localId)
          ? { ...p, status: 'error' as const, error: `HTTP ${resp.status}: ${body.slice(0, 120)}` }
          : p)),
      )
      throw new Error(`Upload failed: HTTP ${resp.status}`)
    }
    const data: ChatUploadResp = await resp.json()
    // Map server-returned attachments back to pending entries by filename
    // order (the Hub returns them in submission order).
    const accepted = data.attachments || []
    const rejected = data.rejected || []
    setChatPending((prev) => {
      const next = [...prev]
      let acceptedIx = 0
      let rejectedIx = 0
      for (const t of toUpload) {
        const slot = next.findIndex((p) => p.localId === t.localId)
        if (slot < 0) continue
        // Match by filename — server preserves the original name.
        const acc = accepted.find((a, i) => i === acceptedIx && a.filename === t.filename)
        if (acc) {
          next[slot] = { ...next[slot], status: 'ready', ref: acc, error: undefined }
          acceptedIx++
          continue
        }
        const rej = rejected[rejectedIx]
        if (rej) {
          next[slot] = { ...next[slot], status: 'error', error: rej.error }
          rejectedIx++
          continue
        }
        next[slot] = { ...next[slot], status: 'error', error: 'no server response' }
      }
      return next
    })
    return [...ready.map((p) => p.ref!).filter(Boolean), ...accepted]
  }

  // Slice 9.5 CP2 (2026-05-19): slash commands intercept chat input
  // before the message hits the overseer LLM. Saves the cost of a
  // chat-turn for simple state queries (cost / budget / insights /
  // working memory / sibling status) and gives Tory a fast keyboard
  // affordance for /clear / /compress / /tick / /help.
  // System bubbles use negative IDs so they never collide with real
  // chat_messages.id values from the DB.
  const slashSystemIdRef = useRef(-1)
  const pushSlashSystemMessage = (content: string) => {
    slashSystemIdRef.current -= 1
    const msg: ChatMessage = {
      id: slashSystemIdRef.current,
      role: 'system',
      content,
      created_at: new Date().toISOString(),
    }
    setChatMessages((prev) => [...prev, msg])
  }

  const SLASH_HELP_TEXT = [
    '## Slash commands',
    '',
    '- `/help` — show this list',
    '- `/clear` — wipe the chat thread (confirm prompt)',
    '- `/compress` — fold older turns into a compressed summary',
    '- `/cost` — show today\'s LLM spend and remaining budget',
    '- `/budget` — alias for /cost',
    '- `/tick` — force an overseer loop tick now',
    '- `/whoami` — working memory snapshot (freshness + posture)',
    '- `/insights` — list pending interpretations',
    '- `/sibling-status` — A-channel dispatch counters',
    '',
    'Commands run locally (no LLM cost). Overseer can still see results in their next turn.',
  ].join('\n')

  const handleSlashCommand = async (raw: string): Promise<boolean> => {
    const parts = raw.trim().split(/\s+/)
    const cmd = (parts[0] || '').toLowerCase()
    if (!cmd.startsWith('/')) return false
    setChatInput('')
    switch (cmd) {
      case '/help': {
        pushSlashSystemMessage(SLASH_HELP_TEXT)
        return true
      }
      case '/clear': {
        if (!confirm('Clear all messages in this thread? The thread itself stays. This cannot be undone.')) return true
        try {
          await apiFetch<any>('/overseer/chat/clear', {
            method: 'POST',
            body: JSON.stringify(
              activeThreadId ? { thread_id: activeThreadId } : {}),
          })
          setChatMessages([])
          chatPending.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl))
          setChatPending([])
          setLastAction('Chat thread cleared')
          await refreshThreads()
        } catch (e: any) {
          pushSlashSystemMessage(`**/clear failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/compress': {
        try {
          const r = await apiFetch<{
            ok: boolean
            messages_before: number
            messages_after: number
            compressed_summary?: string
            cost_usd?: number
            error?: string
          }>('/overseer/chat/compress', {
            method: 'POST',
            body: JSON.stringify(
              activeThreadId ? { thread_id: activeThreadId } : {}),
          })
          if (!r.ok) {
            pushSlashSystemMessage(`**/compress failed:** ${r.error || 'unknown'}`)
          } else {
            pushSlashSystemMessage(
              `**Chat compressed.** ${r.messages_before} → ${r.messages_after} messages ` +
                `(\$${(r.cost_usd ?? 0).toFixed(4)}).\n\n` +
                (r.compressed_summary
                  ? `**Summary written into thread:**\n\n> ${r.compressed_summary.split('\n').join('\n> ')}`
                  : ''),
            )
            await refreshChat()
          }
        } catch (e: any) {
          pushSlashSystemMessage(`**/compress failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/cost':
      case '/budget': {
        try {
          const r = await apiFetch<{
            ok: boolean
            daily?: {
              date: string
              cost_used_usd: number
              cost_max_usd: number
              calls_used: number
              calls_max: number
            }
            session?: { cost_used_usd: number; calls_used: number }
          }>('/overseer/budget')
          const d = r.daily || ({} as any)
          pushSlashSystemMessage(
            [
              '## Budget',
              '',
              `- **Today** (${d.date || '?'}): \$${(d.cost_used_usd ?? 0).toFixed(4)} / \$${(d.cost_max_usd ?? 0).toFixed(2)} ` +
                `· ${d.calls_used ?? 0} / ${d.calls_max ?? 0} calls`,
              `- **Session**: \$${(r.session?.cost_used_usd ?? 0).toFixed(4)} · ${r.session?.calls_used ?? 0} calls`,
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/cost failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/tick': {
        try {
          const r = await apiFetch<any>('/overseer/tick-now', { method: 'POST', body: JSON.stringify({}) })
          const s = r.summary || {}
          // Surface skip reason so a tick held off by an in-flight
          // backfill/drain doesn't read as "nothing happened."
          if (s.skipped) {
            pushSlashSystemMessage(
              `## Tick skipped\n\n_${s.skipped}_\n\nThis usually means a background drain or backfill is currently holding the tick lock. Wait for it to finish, or check **GET /api/overseer/loop** for status.`,
            )
          } else if (r?.ok === false && r?.error) {
            pushSlashSystemMessage(`**/tick failed:** ${r.error}`)
          } else {
            pushSlashSystemMessage(
              [
                '## Tick complete',
                '',
                `- imports_summarized: ${s.imports_summarized ?? 0}`,
                `- notes_tagged: ${s.notes_tagged ?? 0}`,
                `- classify_changed: ${s.classify_changed ?? 0}`,
                `- working_memory_rebuilt: ${s.working_memory_rebuilt ? 'yes' : 'no'}`,
                `- errors: ${(s.errors || []).length || 0}`,
                `- finished: ${s.finished_at || '?'}`,
              ].join('\n'),
            )
          }
        } catch (e: any) {
          pushSlashSystemMessage(`**/tick failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/whoami': {
        try {
          const r = await apiFetch<{ working_memory?: any }>('/overseer/working-memory')
          const wm = r.working_memory || {}
          const built = wm.local_built_at || wm.built_at || '?'
          const lastGist = wm.last_successful_gist_at || '?'
          const queue = wm.import_queue_depth ?? 0
          const dist = wm.recent_gist_source_distribution || {}
          const sibT = wm.sibling_dispatched_today
          const sibC = wm.sibling_daily_cap
          const sibU = wm.sibling_unrated_count
          const sibP = wm.sibling_pending_for_me
          const gitState = wm.git_ingest || {}
          const gitLast = gitState.last_run_at || '?'
          const topByOrigin = Object.entries(dist.by_origin || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ')
          pushSlashSystemMessage(
            [
              '## Working memory snapshot',
              '',
              `- **Built**: ${built}`,
              `- **Last gist**: ${lastGist}`,
              `- **Ingest queue**: ${queue} unprocessed`,
              `- **Recent gists by origin**: ${topByOrigin || '(none)'}`,
              `- **Sibling (Cat A)**: ${sibT}/${sibC} today · unrated ${sibU} · pending-for-me ${sibP}`,
              `- **Git ingest**: last ran ${gitLast}`,
              `- **Open todos**: ${(wm.open_todos || []).length}`,
              `- **Open questions**: ${(wm.open_questions || []).length}`,
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/whoami failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/insights': {
        try {
          const r = await apiFetch<{
            interpretations?: Array<{
              id: number
              kind: string
              title: string
              status: string
            }>
            counts?: Record<string, number>
          }>('/overseer/insight/pending?status=pending')
          const items = r.interpretations || []
          const lines = ['## Pending interpretations', '']
          if (items.length === 0) lines.push('_(none — all reviewed)_')
          else
            items.slice(0, 20).forEach((it) =>
              lines.push(`- **#${it.id}** [${it.kind}] ${it.title}`),
            )
          if (items.length > 20) lines.push('', `_(+${items.length - 20} more)_`)
          pushSlashSystemMessage(lines.join('\n'))
        } catch (e: any) {
          pushSlashSystemMessage(`**/insights failed:** ${e?.message || e}`)
        }
        return true
      }
      case '/sibling-status': {
        try {
          const r = await apiFetch<{ working_memory?: any }>('/overseer/working-memory')
          const wm = r.working_memory || {}
          pushSlashSystemMessage(
            [
              '## Sibling status (Category A)',
              '',
              `- Dispatched today: **${wm.sibling_dispatched_today ?? 0} / ${wm.sibling_daily_cap ?? 0}**`,
              `- Unrated completed (overseer owes a rating): **${wm.sibling_unrated_count ?? 0}**`,
              `- In flight (dispatched, awaiting result): **${wm.sibling_pending_for_me ?? 0}**`,
              '',
              '_Category B and C agents are gated on ≥1 week of A-volume data + graduation criteria._',
            ].join('\n'),
          )
        } catch (e: any) {
          pushSlashSystemMessage(`**/sibling-status failed:** ${e?.message || e}`)
        }
        return true
      }
      default: {
        pushSlashSystemMessage(`Unknown command \`${cmd}\`. Type \`/help\` for the list.`)
        return true
      }
    }
  }

  // Slice 14: voice-mode turn. Sends a spoken transcript through the
  // overseer chat with voice_mode=true (Pi appends the succinctness
  // directive), refreshes the thread so the bubbles render, and
  // returns the reply text for the hook to speak aloud.
  const handleVoiceTurn = async (text: string): Promise<string> => {
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setChatMessages((prev) => [...prev, optimistic])
    // Voice turns must also set chatSending so the thread sidebar
    // locks while the turn is in flight, and pin the rendered thread
    // so the exchange can't split across threads.
    setChatSending(true)
    try {
      const r = await apiFetch<ChatSendResp>('/overseer/chat', {
        method: 'POST',
        body: JSON.stringify({
          message: text,
          voice_mode: true,
          ...(activeThreadId ? { thread_id: activeThreadId } : {}),
        }),
      })
      await Promise.all([refreshChat(), refreshThreads()])
      if (!r.ok) {
        setError(`Voice chat error: ${r.error || 'unknown'}`)
        return ''
      }
      return r.reply || ''
    } catch (e: any) {
      setError(`Voice chat failed: ${e?.message || e}`)
      return ''
    } finally {
      setChatSending(false)
    }
  }

  const voice = useVoiceMode({ sendVoiceTurn: handleVoiceTurn })
  // The Voice tab drives the Pipecat agent, a local WebRTC sidecar with
  // no cloud path; hide it in the cloud Hub. The chat mic (voice mode)
  // stays: it uses the gateway's Groq STT + ElevenLabs TTS routes.
  const { cloud: isCloud } = useCloudMode()

  const handleSendChat = async () => {
    const message = chatInput.trim()
    // Slice 9.5 CP2: slash command intercept BEFORE the cost-of-a-turn
    // gate. Falls through to the LLM only for non-slash messages.
    if (message.startsWith('/')) {
      await handleSlashCommand(message)
      return
    }
    const queuedReady = chatPending.filter((p) => p.status === 'queued' && !p.error)
    const alreadyReady = chatPending.filter((p) => p.status === 'ready' && p.ref)
    const hasFiles = queuedReady.length + alreadyReady.length > 0
    if ((!message && !hasFiles) || chatSending) return

    setChatSending(true)
    setError('')

    // Step 1: upload any pending attachments first. If the user has
    // already retried after errors, only the still-queued ones go up
    // again. We block the send if uploads fail outright (network /
    // HTTP error) so the user can decide whether to remove or retry.
    let attachmentRefs: ChatAttachmentRef[] = []
    if (hasFiles) {
      try {
        attachmentRefs = await uploadPendingAttachments()
      } catch (e: any) {
        setError(`Attachment upload failed: ${e?.message || e}`)
        setChatSending(false)
        return
      }
      if (attachmentRefs.length === 0) {
        setError('All attachments were rejected — see errors on each file.')
        setChatSending(false)
        return
      }
    }

    // Step 2: optimistic user-message bubble locally (with attachment
    // previews so the user sees what they're sending immediately).
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: 'user',
      content: message || (attachmentRefs.length
        ? `(see attached file${attachmentRefs.length === 1 ? '' : 's'})` : ''),
      created_at: new Date().toISOString(),
      attachments: attachmentRefs.map((r, i) => ({
        id: -i - 1,
        chat_message_id: -Date.now(),
        filename: r.filename, mime_type: r.mime_type,
        size_bytes: r.size, kind: r.kind || classifyKind(r.filename, r.mime_type),
        pi_path: r.pi_path, file_id: r.file_id ?? 0,
        sha256: r.sha256 || '',
      })),
    }
    setChatMessages((prev) => [...prev, optimistic])
    setChatInput('')

    // Clear pending — ObjectURLs get revoked so we don't leak.
    chatPending.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl))
    setChatPending([])

    // Slice 14.7 CP4: route between /quick-chat (router default) and
    // /chat (full Opus overseer). Attachments force /chat because the
    // router endpoint doesn't accept them. Direct mode forces /chat
    // too. Otherwise: thin Flash path.
    const useRouter = !directMode && attachmentRefs.length === 0
    try {
      if (useRouter) {
        const r = await apiFetch<ChatSendResp>('/overseer/quick-chat', {
          method: 'POST',
          body: JSON.stringify({
            message: message || '',
            direct_override: false,
            // Pin the thread this window is rendering: without it,
            // a pointer move from another surface mid-request would
            // redirect the turn.
            ...(activeThreadId ? { thread_id: activeThreadId } : {}),
          }),
        })
        if (!r.ok) setError(`Chat error: ${r.error || 'unknown'}`)
      } else {
        const body: any = { message: message || '' }
        if (attachmentRefs.length) body.attachments = attachmentRefs
        if (activeThreadId) body.thread_id = activeThreadId
        const r = await apiFetch<ChatSendResp>('/overseer/chat', {
          method: 'POST',
          body: JSON.stringify(body),
        })
        if (!r.ok) setError(`Chat error: ${r.error || 'unknown'}`)
      }
      // Always re-fetch so we get the persisted IDs + assistant reply +
      // attachments-from-DB + the answered_by tag (replaces the
      // optimistic stub above). Threads too — the send may have
      // auto-titled the thread and bumped its count.
      await Promise.all([refreshChat(), refreshThreads()])
    } catch (e: any) {
      setError(`Chat failed: ${e?.message || e}`)
    } finally {
      setChatSending(false)
    }
  }

  const handleClearChat = async () => {
    if (!confirm('Clear all messages in this thread? The thread itself stays. This cannot be undone.')) return
    try {
      // Pin the rendered thread: the confirm dialog holds the user
      // for seconds, plenty of time for the active pointer to move.
      await apiFetch<any>('/overseer/chat/clear', {
        method: 'POST',
        body: JSON.stringify(
          activeThreadId ? { thread_id: activeThreadId } : {}),
      })
      setChatMessages([])
      // Clear any in-flight composer attachments too.
      chatPending.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl))
      setChatPending([])
      setLastAction('Chat thread cleared')
      await refreshThreads()
    } catch (e: any) {
      setError(`Clear failed: ${e?.message || e}`)
    }
  }

  const handleDismissNotification = async (id: number) => {
    try {
      await apiFetch<any>('/overseer/notifications/dismiss', {
        method: 'POST',
        body: JSON.stringify({ id }),
      })
      await refreshAll()
    } catch (e: any) {
      setError(`Dismiss failed: ${e?.message || e}`)
    }
  }

  const handleDismissAllNotifications = async () => {
    try {
      await apiFetch<any>('/overseer/notifications/dismiss', {
        method: 'POST',
        body: JSON.stringify({ all: true }),
      })
      await refreshAll()
    } catch (e: any) {
      setError(`Dismiss-all failed: ${e?.message || e}`)
    }
  }

  const refreshGraph = async () => {
    setGraphLoading(true)
    setGraphError('')
    try {
      const r = await apiFetch<GraphResp>('/overseer/explorer/graph')
      if (!r.ok) {
        setGraphError(r.error || 'graph fetch failed')
      } else {
        setGraph(r)
      }
    } catch (e: any) {
      setGraphError(e?.message || String(e))
    } finally {
      setGraphLoading(false)
    }
  }

  // Slice 9.6 CP1: respond to a custom action button on a notification
  // (free_text / yes_no / dispatch_sibling / predefined CRUD kind). The
  // response is logged in notification_responses and surfaced to
  // overseer on next tick via the freshness block + the
  // get_pending_notification_responses tool.
  const handleNotificationRespond = async (
    notification_id: number,
    action_kind: string,
    action_label: string,
    response_payload: Record<string, any> = {},
    also_archive: boolean = true,
  ) => {
    try {
      await apiFetch<any>('/overseer/notifications/respond', {
        method: 'POST',
        body: JSON.stringify({
          notification_id,
          action_kind,
          action_label,
          response_payload,
          also_archive,
        }),
      })
      await refreshAll()
      setLastAction(
        `Responded to #${notification_id} (${action_kind})${
          also_archive ? ' + archived' : ''
        }`,
      )
    } catch (e: any) {
      setError(`Notification respond failed: ${e?.message || e}`)
    }
  }

  const handleNotificationAction = async (
    id: number,
    action: 'archive' | 'snooze' | 'touch',
    snooze_days: number = 30,
  ) => {
    try {
      const body: { id: number; action: string; snooze_days?: number } = {
        id, action,
      }
      if (action === 'snooze') body.snooze_days = snooze_days
      await apiFetch<any>('/overseer/notifications/action', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      await refreshAll()
      if (action === 'snooze') {
        setLastAction(`Snoozed #${id} for ${snooze_days}d`)
      } else if (action === 'archive') {
        setLastAction(`Archived #${id}`)
      } else {
        setLastAction(`Brought #${id} back to top`)
      }
    } catch (e: any) {
      setError(`Notification ${action} failed: ${e?.message || e}`)
    }
  }

  const handleOpenNotificationInChat = (n: NotificationRow) => {
    // Pre-fill a chat prompt from the notification's content and switch
    // to the chat tab. The user can edit before sending. This is the
    // minimal feedback loop — full per-rule actions (Archive/Snooze/etc.)
    // come in 3f.
    const ruleHints: Record<string, string> = {
      stale_active_project:
        "Should I archive this, ramp it back up, or is the staleness fine?",
      automation_anomaly:
        "What likely went wrong, and is it worth investigating?",
      import_backlog:
        "Should I just kick off /backfill, or is there a better strategy?",
    }
    const hint = ruleHints[n.rule_name] ||
      "What do you make of this, and what should I do?"
    const prefill = [
      `You flagged: "${n.title}"`,
      n.body ? `\nDetails: ${n.body}` : "",
      `\nRule: ${n.rule_name}, key: ${n.rule_key}`,
      `\n\n${hint}`,
    ].join("")
    setChatInput(prefill)
    setTab('chat')
  }

  const handleScan = async () => {
    setBusy('Scanning ~/.claude/projects/...')
    setLastAction('')
    setError('')
    try {
      const r = await apiFetch<ScanResp>('/overseer/scan/claude-code')
      setScan(r)
      setLastAction(`Scan found ${r.total ?? 0} Claude Code session files`)
      // Polish CP3: use the SERVER-side already_imported flag instead
      // of joining against the client-side imports list (which is
      // capped at 200 rows and was wrongly auto-selecting ~250 already-
      // imported files for users with 200+ imports). The polish CP1
      // scan endpoint hashes each file and marks already_imported
      // authoritatively.
      const sel = new Set<string>()
      for (const f of r.found || []) {
        if (!f.already_imported) sel.add(f.path)
      }
      setSelectedPaths(sel)
    } catch (e: any) {
      setError(`Scan failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleImportSelected = async () => {
    if (selectedPaths.size === 0) return
    const paths = Array.from(selectedPaths)
    setBusy(`Uploading ${paths.length} file(s) to Pi…`)
    setLastAction('')
    setError('')
    try {
      const r = await apiFetch<any>('/overseer/import', {
        method: 'POST',
        body: JSON.stringify({
          paths,
          source: 'claude-code',
          skip_already_imported: true,
        }),
      })
      const c = r?.counts || {}
      setLastAction(
        `Import: ${c.imported ?? 0} new, ${c.skipped ?? 0} already on Pi (dedup), ${c.failed ?? 0} failed (of ${c.requested ?? 0})`
      )
      setSelectedPaths(new Set())
      await refreshAll()
    } catch (e: any) {
      setError(`Import failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleTickNow = async () => {
    setBusy('Running overseer tick…')
    setLastAction('')
    setError('')
    try {
      const r = await apiFetch<any>('/overseer/tick-now', {
        method: 'POST',
        body: JSON.stringify({}),
      })
      const s = r?.summary || {}
      // 14.7.2 (2026-05-26): the Pi-side tick endpoint returns
      // {"ok": false, "summary": {"skipped": "another tick is
      // already running"}} when the tick lock is held — typically
      // by a background drain (/imports/process-targeted, /backfill).
      // Previously we displayed "sessions=0 imports=0 notes=0" in
      // that case, which read as "nothing happened" — silently
      // misleading. Surface the skip reason instead.
      if (s.skipped) {
        setLastAction(`Tick skipped — ${s.skipped}`)
      } else if (r?.ok === false && r?.error) {
        setError(`Tick failed: ${r.error}`)
      } else {
        setLastAction(
          `Tick: sessions=${s.sessions_summarized ?? 0}, imports=${s.imports_summarized ?? 0}, notes=${s.notes_tagged ?? 0}, calls=${s.budget?.calls_used ?? 0}, cost=$${s.budget?.cost_used_usd ?? 0}`
        )
      }
      await refreshAll()
    } catch (e: any) {
      setError(`Tick failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const handleDeleteImport = async (id: string) => {
    if (!confirm(`Delete import ${id}? The .jsonl on Pi will be removed.`)) return
    setBusy(`Deleting ${id}…`)
    setError('')
    try {
      await apiFetch<any>('/overseer/imports/delete', {
        method: 'POST',
        body: JSON.stringify({ id, remove_file: true }),
      })
      await refreshAll()
    } catch (e: any) {
      setError(`Delete failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  const toggleSelect = (path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-semibold text-text-primary">
              {tab === 'chat' ? 'Chat' : 'Corpus'}
            </h2>
            {status?.loop_running ? (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-success/20 text-success">
                Loop running
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-text-muted/20 text-text-muted">
                Loop idle
              </span>
            )}
            {budget && (
              <BudgetIndicator budget={budget} />
            )}
            {status?.working_memory_built_at && (
              <span className="text-xs text-text-muted">
                wm built {fmtRelative(status.working_memory_built_at)}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {busy && (
              <span className="text-xs text-text-muted">{busy}</span>
            )}
            {tab !== 'chat' && (
            <div className="flex gap-1 bg-surface-tertiary rounded-lg p-0.5">
              {([
                ['overview', 'Overview'],
                ['insights', `Insights${insightCounts && insightCounts.pending > 0 ? ` (${insightCounts.pending})` : ''}`],
                ['projects', 'Projects'],
                ['squeeze', 'Squeeze'],
                ['ecosystem', 'Map'],
                ['explorer', 'Explorer'],
                ['contacts', 'Contacts'],
                ...(isCloud ? [] : [['voice', 'Voice'] as const]),
                ['notifications', `Bell${notificationsUnread > 0 ? ` (${notificationsUnread})` : ''}`],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setTab(id as Tab)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                    tab === id
                      ? 'bg-accent text-white'
                      : 'text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            )}
            <button
              onClick={refreshAll}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
            >
              Refresh
            </button>
            <button
              onClick={handleTickNow}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              Tick Now
            </button>
          </div>
        </div>
        {lastAction && (
          <div className="mt-2 text-xs text-success">{lastAction}</div>
        )}
        {error && (
          <div className="mt-2 text-xs text-red-400">{error}</div>
        )}
      </div>

      {/* Body */}
      {tab === 'chat' && (
        <ChatPanel
          messages={chatMessages}
          input={chatInput}
          setInput={setChatInput}
          sending={chatSending}
          onSend={handleSendChat}
          onClear={handleClearChat}
          onRefresh={refreshChat}
          pending={chatPending}
          onAddFiles={handleAddPendingFiles}
          onRemovePending={handleRemovePending}
          voiceState={voice.voiceState}
          voiceError={voice.lastError}
          voiceLastHeard={voice.lastHeard}
          onEnterVoice={voice.enterVoiceMode}
          onExitVoice={voice.exitVoiceMode}
          directMode={directMode}
          onToggleDirectMode={() => setDirectMode((v) => !v)}
          threads={chatThreads}
          activeThreadId={activeThreadId}
          onNewThread={handleNewThread}
          onSelectThread={handleSelectThread}
          onRenameThread={handleRenameThread}
          onDeleteThread={handleDeleteThread}
          prompts={chatPrompts}
          onSavePrompt={handleSavePrompt}
          onDeletePrompt={handleDeletePrompt}
          onSendFeedback={handleSendFeedback}
          onDiscussFeedback={handleDiscussFeedback}
        />
      )}
      {tab === 'contacts' && <ContactsPanel />}
      {tab === 'voice' && <VoicePanel />}
      {tab === 'notifications' && (
        <NotificationsPanel
          notifications={notifications}
          onDismiss={handleDismissNotification}
          onDismissAll={handleDismissAllNotifications}
          onOpenInChat={handleOpenNotificationInChat}
          onAction={handleNotificationAction}
          onRespond={handleNotificationRespond}
        />
      )}
      {tab === 'explorer' && (
        <Suspense fallback={<PanelLoading label="graph engine" />}>
          <ExplorerPanel
            graph={graph}
            loading={graphLoading}
            error={graphError}
            onRefresh={refreshGraph}
            onTokenClick={(t) => {
              // Same UX as Insights: jump to Overview, open DetailCard.
              setTab('overview')
              setExpandedToken(t)
            }}
          />
        </Suspense>
      )}
      {tab === 'ecosystem' && (
        <Suspense fallback={<PanelLoading label="graph engine" />}>
          <EcosystemMapPanel />
        </Suspense>
      )}
      {tab === 'projects' && (
        <ProjectsTab />
      )}
      {tab === 'squeeze' && (
        <SqueezePanel />
      )}
      {tab === 'insights' && (
        <InsightsPanel
          interpretations={insights}
          counts={insightCounts}
          scans={insightScansHistory}
          statusFilter={insightStatusFilter}
          setStatusFilter={(s) => {
            setInsightStatusFilter(s)
            refreshInsights(s)
          }}
          scanProject={insightScanProject}
          setScanProject={setInsightScanProject}
          scanDays={insightScanDays}
          setScanDays={setInsightScanDays}
          onScanNow={handleInsightScanNow}
          onDistillCorrections={handleDistillCorrections}
          onDecide={handleInsightDecide}
          editingId={editingInsightId}
          setEditing={(id, title, body) => {
            setEditingInsightId(id)
            setEditTitle(title)
            setEditBody(body)
          }}
          editTitle={editTitle}
          editBody={editBody}
          setEditTitle={setEditTitle}
          setEditBody={setEditBody}
          onTokenClick={(t) => {
            setTab('overview')
            setExpandedToken(t)
          }}
          busy={busy}
        />
      )}
      {tab === 'overview' && (
      <div className="flex-1 overflow-y-auto p-6 space-y-6">

        {/* Stats grid */}
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            label="Gists"
            value={status?.overseer_db?.summaries_gist ?? 0}
            sub={`${status?.overseer_db?.processed_sessions ?? 0} sessions, ${status?.overseer_db?.processed_imported_sessions ?? 0} imports`}
          />
          <StatCard
            label="Themes"
            value={status?.overseer_db?.summaries_theme ?? 0}
            sub={`${status?.overseer_db?.summaries_episode ?? 0} episodes`}
          />
          <StatCard
            label="Open Questions"
            value={status?.overseer_db?.open_questions ?? 0}
            sub={`${status?.overseer_db?.patterns ?? 0} patterns, ${status?.overseer_db?.drift_observations ?? 0} drift`}
          />
          <StatCard
            label="LLM Calls"
            value={status?.overseer_db?.llm_calls ?? 0}
            sub={
              llmStats?.stats?.[0]
                ? `$${(llmStats.stats[0].total_cost_usd ?? 0).toFixed(4)} last ${llmStats.period_days}d`
                : '—'
            }
          />
        </section>

        {/* Slice 3f.5 #4: blindspots indicator */}
        {blindspots.length > 0 && (
          <Card title={`Known blindspots (${blindspots.length} active — apply to interpretations below)`}>
            <ul className="space-y-2 text-xs">
              {blindspots.slice(0, 8).map((b) => (
                <li key={b.id} className="flex gap-2">
                  <span className="text-text-muted font-mono shrink-0">
                    {b.model_pattern}
                  </span>
                  <span className="text-text-secondary">{b.body}</span>
                  {b.confidence_adjustment !== 0 && (
                    <span className={`shrink-0 text-[10px] uppercase font-medium ${
                      b.confidence_adjustment > 0 ? 'text-amber-400' : 'text-text-muted'
                    }`}>
                      {b.confidence_adjustment > 0 ? '+1 conf' : '-1 conf'}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}

        {/* Working memory */}
        <Card title="Working Memory">
          {!wm?.working_memory ? (
            <div className="text-sm text-text-muted">
              {wm?.hint || 'No working memory yet.'}
            </div>
          ) : (
            <WorkingMemoryView
              wm={wm.working_memory}
              expandedToken={expandedToken}
              onTokenClick={(t) =>
                setExpandedToken((prev) => (prev === t ? null : t))
              }
              onCloseDetail={() => setExpandedToken(null)}
            />
          )}
        </Card>

        {/* Imports — visual refresh in Polish CP3.
            Renamed from "Imported Claude Sessions" once the historical
            ChatGPT bulk import landed (1,728 sessions); the panel now
            covers any AI conversation source. The Scan flow remains
            Claude-Code specific (~/.claude/projects/) — other sources
            come in via direct import paths. */}
        <Card title="Imported AI Conversations">
          {/* Top action row + summary stat-line */}
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <button
              onClick={handleScan}
              disabled={!!busy}
              className="px-3.5 py-2 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50 transition-colors"
            >
              {scan ? 'Re-scan' : 'Scan ~/.claude/projects/'}
            </button>
            <button
              onClick={handleImportSelected}
              disabled={!!busy || selectedPaths.size === 0}
              className="px-3.5 py-2 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-30 transition-colors"
            >
              Import{selectedPaths.size > 0 ? ` ${selectedPaths.size} selected` : ''}
            </button>
            <div className="ml-auto flex items-center gap-3 text-[11px]">
              <span className="text-text-muted">
                <span className="text-text-primary font-medium">{imports.length}</span> on Pi
              </span>
              {scan && (
                <>
                  <span className="text-text-muted">·</span>
                  <span className="text-success">
                    <span className="font-medium">{scan.new_count ?? 0}</span> new
                  </span>
                  <span className="text-text-muted">/</span>
                  <span className="text-text-muted">
                    <span className="text-text-secondary font-medium">{scan.already_imported_count ?? 0}</span> already imported
                  </span>
                  <span className="text-text-muted">(of {scan.total ?? '—'} local)</span>
                </>
              )}
            </div>
          </div>

          {/* Empty state when scan hasn't run */}
          {!scan && (
            <div className="border border-dashed border-border rounded-lg p-6 text-center text-xs text-text-muted">
              Scan to see local Claude Code sessions on this machine.
              The Pi will hash them, mark which are already imported, and let
              you pick which new ones to upload.
              <div className="mt-2 text-[11px] text-text-muted/70">
                Other sources (ChatGPT, etc.) come in via direct import paths
                and appear in the table below tagged with their source.
              </div>
            </div>
          )}

          {scan && scan.found && scan.found.length > 0 && (() => {
            // Polish CP3: group scanned files by project_folder so a
            // 430-file scan reads as ~30 collapsed groups instead of
            // a flat scroll. Each group exposes "select all new" for
            // the common case ("import all new sessions in Cortex").
            const groups = scan.found.reduce<Map<string, ScanRow[]>>((acc, f) => {
              const k = f.project_folder || '(unknown)'
              const list = acc.get(k) || []
              list.push(f)
              acc.set(k, list)
              return acc
            }, new Map())
            // Sort: groups with new items first, then by row count desc
            const sorted = Array.from(groups.entries()).sort((a, b) => {
              const newA = a[1].filter((f) => !f.already_imported).length
              const newB = b[1].filter((f) => !f.already_imported).length
              if (newA !== newB) return newB - newA
              return b[1].length - a[1].length
            })
            return (
              <ul className="border border-border rounded-lg overflow-hidden divide-y divide-border max-h-[28rem] overflow-y-auto">
                {sorted.map(([folder, rows]) => {
                  const newRows = rows.filter((f) => !f.already_imported)
                  const knownCount = rows.length - newRows.length
                  const allNewSelected = newRows.length > 0 &&
                    newRows.every((f) => selectedPaths.has(f.path))
                  const isOpen = openImportGroups.has(folder)
                  // Use the imported project name (cwd basename) when
                  // available — that's typically the cleaner display
                  // name compared to the encoded folder.
                  const matchedImport = imports.find(
                    (i) => i.project && rows.some(
                      (r) => i.id.split(':').slice(1).join(':') === r.session_id,
                    ),
                  )
                  const displayName = matchedImport?.project || folder
                  return (
                    <li key={folder} className="bg-surface-secondary/30 transition-colors hover:bg-surface-secondary/60">
                      {/* Group header — entire row toggles the group.
                          The "Select all new" button stops propagation
                          so it doesn't accidentally collapse the group
                          when the user means to bulk-select. */}
                      <div
                        onClick={() => toggleImportGroup(folder)}
                        className="px-3 py-2.5 flex items-center gap-2 flex-wrap cursor-pointer select-none"
                      >
                        <span
                          className={`text-text-muted text-[11px] tracking-wide w-5 inline-block transition-transform ${
                            isOpen ? 'rotate-90' : ''
                          }`}
                        >
                          ▸
                        </span>
                        <span className="text-sm text-text-primary font-medium truncate max-w-md">
                          {displayName}
                        </span>
                        {displayName !== folder && (
                          <span className="text-[10px] text-text-muted/60 font-mono truncate max-w-xs">
                            {folder}
                          </span>
                        )}
                        <div className="ml-auto flex items-center gap-1.5 text-[10px]">
                          {newRows.length > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-success/15 text-success font-semibold uppercase tracking-wide">
                              {newRows.length} new
                            </span>
                          )}
                          {knownCount > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-text-muted/10 text-text-muted/80 uppercase tracking-wide">
                              {knownCount} on Pi
                            </span>
                          )}
                          {newRows.length > 0 && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                const next = new Set(selectedPaths)
                                if (allNewSelected) {
                                  newRows.forEach((f) => next.delete(f.path))
                                } else {
                                  newRows.forEach((f) => next.add(f.path))
                                }
                                setSelectedPaths(next)
                              }}
                              className="text-[10px] uppercase tracking-wide text-accent-hover hover:text-accent cursor-pointer ml-1.5 px-2 py-0.5 rounded hover:bg-accent/10 transition-colors"
                            >
                              {allNewSelected ? 'Deselect new' : 'Select all new'}
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Group body — only when expanded */}
                      {isOpen && (
                        <ul className="border-t border-border bg-surface/30">
                          {rows.map((f) => {
                            const known = !!f.already_imported
                            const matched = known
                              ? imports.find(
                                  (i) =>
                                    i.id.split(':').slice(1).join(':') === f.session_id,
                                )
                              : undefined
                            return (
                              <li
                                key={f.path}
                                className={`pl-9 pr-3 py-1.5 flex items-center gap-3 text-[11px] transition-colors ${
                                  known
                                    ? 'opacity-50 hover:bg-surface-tertiary/20'
                                    : 'hover:bg-accent/5'
                                }`}
                              >
                                <input
                                  type="checkbox"
                                  checked={selectedPaths.has(f.path)}
                                  onChange={() => toggleSelect(f.path)}
                                  disabled={known}
                                  className="shrink-0 accent-accent cursor-pointer disabled:cursor-not-allowed"
                                />
                                <span className="text-text-muted/70 font-mono shrink-0 w-20">
                                  {f.session_id.slice(0, 8)}
                                </span>
                                {known ? (
                                  <span className="shrink-0 text-[9px] uppercase tracking-wider text-text-muted/70">
                                    on Pi
                                  </span>
                                ) : (
                                  <span className="shrink-0 text-[9px] uppercase tracking-wider text-success font-semibold">
                                    new
                                  </span>
                                )}
                                {matched?.project && matched.project !== folder && (
                                  <span className="text-[10px] text-text-muted truncate max-w-xs italic">
                                    → {matched.project}
                                  </span>
                                )}
                                <span className="ml-auto text-text-muted/70 shrink-0">
                                  {fmtBytes(f.size_bytes)}
                                </span>
                                <span className="text-text-muted/70 shrink-0 w-20 text-right">
                                  {fmtRelative(f.mtime_iso)}
                                </span>
                              </li>
                            )
                          })}
                        </ul>
                      )}
                    </li>
                  )
                })}
              </ul>
            )
          })()}

          {imports.length > 0 && (
            <div className="border border-border rounded-md max-h-72 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-surface-tertiary sticky top-0">
                  <tr>
                    <th className="text-left p-2">Project</th>
                    <th className="text-left p-2">Session</th>
                    <th className="text-right p-2">Msgs</th>
                    <th className="text-right p-2">Duration</th>
                    <th className="text-right p-2">Size</th>
                    <th className="text-center p-2">Status</th>
                    <th className="text-right p-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {imports.map((i) => (
                    <tr key={i.id} className="border-t border-border">
                      <td className="p-2 text-text-primary truncate max-w-xs">
                        <div className="flex items-center gap-1.5">
                          <SourceBadge source={i.source} />
                          <span className="truncate">
                            {i.project || '(unknown)'}
                          </span>
                        </div>
                      </td>
                      <td className="p-2 text-text-muted font-mono">
                        {i.id.split(':')[1]?.slice(0, 8) || i.id}
                      </td>
                      <td className="p-2 text-right text-text-muted">
                        {i.message_count}
                      </td>
                      <td className="p-2 text-right text-text-muted">
                        {fmtDuration(i.duration_minutes)}
                      </td>
                      <td className="p-2 text-right text-text-muted">
                        {fmtBytes(i.bytes_size)}
                      </td>
                      <td className="p-2 text-center">
                        {i.processed ? (
                          <span className="text-success">summarized</span>
                        ) : (
                          <span className="text-text-muted">queued</span>
                        )}
                      </td>
                      <td className="p-2 text-right">
                        <button
                          onClick={() => handleDeleteImport(i.id)}
                          disabled={!!busy}
                          className="text-xs text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Loop + LLM */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card title="Background Loop">
            <dl className="text-xs space-y-1">
              <Row label="Running" value={loop?.running ? 'yes' : 'no'} />
              <Row label="Ticks run" value={loop?.ticks_run ?? 0} />
              <Row label="Failed" value={loop?.ticks_failed ?? 0} />
              <Row
                label="Last tick"
                value={fmtRelative(loop?.last_tick_at)}
              />
              <Row
                label="Started"
                value={fmtRelative(loop?.started_at)}
              />
              {loop?.last_error && (
                <Row label="Last error" value={loop.last_error} />
              )}
            </dl>
          </Card>

          <Card title="LLM Cost (last 7 days)">
            {!llmStats?.stats?.length ? (
              <div className="text-xs text-text-muted">No calls yet</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted">
                    <th className="text-left">Backend</th>
                    <th className="text-right">Calls</th>
                    <th className="text-right">Avg ms</th>
                    <th className="text-right">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {llmStats.stats.map((s) => (
                    <tr key={s.actual_backend} className="border-t border-border">
                      <td className="py-1">{s.actual_backend}</td>
                      <td className="py-1 text-right">
                        {s.oks}/{s.calls}
                      </td>
                      <td className="py-1 text-right">{s.avg_ms}</td>
                      <td className="py-1 text-right">
                        ${(s.total_cost_usd ?? 0).toFixed(4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      </div>
      )}
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────

function BudgetIndicator({ budget }: { budget: BudgetSnapshot }) {
  const pctCost = budget.cost_max_usd > 0
    ? Math.min(100, Math.round((budget.cost_used_usd / budget.cost_max_usd) * 100))
    : 0
  const cls = budget.exhausted
    ? 'text-red-400'
    : pctCost > 70
      ? 'text-amber-400'
      : 'text-text-muted'
  return (
    <span className={`text-xs ${cls}`} title={`Daily cap: ${budget.cost_max_usd} / ${budget.calls_max} calls. Resets at UTC midnight.`}>
      Today: ${budget.cost_used_usd.toFixed(2)} / ${budget.cost_max_usd.toFixed(2)}
      {' '}({budget.calls_used}/{budget.calls_max} calls)
    </span>
  )
}

