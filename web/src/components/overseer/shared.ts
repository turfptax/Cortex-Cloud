/** Shared types, constants, and format helpers for the Overseer page
 * and its panels. Moved verbatim out of OverseerPage.tsx as step A of
 * the CP5 split (see memory: cortex-desktop-redesign-plan). */

// ── Types ─────────────────────────────────────────────────────

export interface StatusResp {
  ok: boolean
  plugin?: string
  version?: string
  overseer_db?: Record<string, number>
  core_memory_open?: boolean
  core_db_path?: string
  core_stats?: Record<string, any>
  loop_running?: boolean
  last_tick_at?: string | null
  working_memory_built_at?: string | null
  llm_default_backend?: string
  seed_summary?: any
  error?: string
}

export interface WMQuestionEvidence {
  contribution: string
  reason: string
  evidence_table: string
  evidence_id: number
  evidence_body?: string
  evidence_label?: string
  evidence_confidence?: string
  evidence_created_at?: string
  token?: string                 // 3g #2
}

export interface WMTopQuestion {
  id: number
  token?: string                 // 3g #2
  question: string
  body?: string
  confidence: string
  lifecycle: string
  evidence_count: number
  tags?: string[]
  recent_evidence?: WMQuestionEvidence[]
}

export interface WMUnfiledGist {
  id: number
  token?: string                 // 3g #2
  body: string
  period_label?: string
  created_at?: string
}

// Slice 3g: depth signals
export interface WMPattern {
  id: number
  token?: string
  name: string
  body: string
  confidence?: string
  occurrences?: number
  last_observed_at?: string
}

export interface WMDrift {
  id: number
  token?: string
  body: string
  direction?: string
  confidence?: string
  observed_at?: string
}

export interface WMFutureNote {
  id: number
  token?: string
  instance_id: string
  written_at?: string
  body: string
}

export interface WMRollup {
  id?: number
  token?: string
  project: string
  rollup_date: string
  session_count?: number
  total_minutes?: number
  median_minutes?: number
  summary: string
}

// Slice 3g #2: drill-down detail response
export interface DetailNextToken {
  token: string
  label: string
  kind?: string
}

export interface DetailResp {
  ok: boolean
  token?: string
  type?: string
  primary?: Record<string, any>
  tags?: string[]
  context?: Record<string, any>
  next_tokens?: DetailNextToken[]
  error?: string
}

export interface WorkingMemory {
  built_at?: string
  schema_version?: number
  top_questions?: WMTopQuestion[]   // Slice 3f.5 #2: PRIMARY view
  top_projects?: Array<{
    tag: string
    name: string
    last_touched: string
    description?: string
  }>
  recent_decisions?: Array<{ id: number; content: string; created_at: string }>
  open_todos?: Array<{ id: number; content: string; created_at: string }>
  open_questions?: Array<{   // legacy back-compat
    id: number
    question: string
    confidence: string
    tags?: string[]
  }>
  recent_themes?: Array<{ id: number; token?: string; title: string; confidence: string }>
  recent_episode_titles?: string[]
  last_week_digest?: string
  unfiled_recent_gists?: WMUnfiledGist[]
  future_overseer_notes_count?: number
  journal_entry_count?: number
  blindspots?: BlindspotRow[]
  // Slice 3g: depth signals
  recent_patterns?: WMPattern[]
  recent_drift?: WMDrift[]
  recent_future_notes?: WMFutureNote[]
  recent_rollups?: WMRollup[]
}

export interface WorkingMemoryResp {
  ok: boolean
  working_memory?: WorkingMemory | null
  source?: string
  working_memory_status?: string
  hint?: string
}

export interface ImportRow {
  id: string
  source: string
  source_path: string
  project: string
  cwd: string
  git_branch: string
  started_at: string | null
  ended_at: string | null
  duration_minutes: number
  message_count: number
  user_message_count: number
  assistant_message_count: number
  tool_use_count: number
  bytes_size: number
  file_hash: string
  imported_at: string
  processed: boolean
}

export interface ImportsResp {
  ok: boolean
  imports?: ImportRow[]
  total?: number
}

export interface ScanRow {
  path: string
  session_id: string
  project_folder: string
  size_bytes: number
  mtime: number
  mtime_iso: string
  // Polish slice CP1: server-side authoritative flag
  file_hash?: string
  already_imported?: boolean
}

export interface ScanResp {
  ok: boolean
  found?: ScanRow[]
  total?: number
  scanned_dir?: string
  note?: string
  already_imported_count?: number
  new_count?: number
}

export interface LoopResp {
  ok: boolean
  started_at?: string
  ticks_run?: number
  ticks_failed?: number
  last_tick_at?: string | null
  last_tick_summary?: any
  last_error?: string
  running?: boolean
}

export interface LlmStatsResp {
  ok: boolean
  stats?: Array<{
    actual_backend: string
    calls: number
    oks: number
    degraded_calls: number
    avg_ms: number
    total_cost_usd: number
  }>
  period_days?: number
}

// Slice 8: file attachments on chat
export type ChatAttachmentKind = 'image' | 'text' | 'pdf' | 'other'

export interface ChatAttachmentRef {
  // Returned from POST /api/overseer/chat/upload and submitted back
  // with POST /api/overseer/chat. The Pi reads bytes from pi_path.
  filename: string
  mime_type: string
  size: number
  pi_path: string
  file_id?: number | null
  sha256?: string
  kind?: ChatAttachmentKind
}

export interface ChatStoredAttachment {
  // Shape returned by Pi on /chat/history per message (chat_message_files row)
  id: number
  chat_message_id: number
  filename: string
  mime_type: string
  size_bytes: number
  kind: ChatAttachmentKind
  pi_path: string
  file_id: number
  sha256: string
  created_at?: string
}

// Slice 9.5 CP1 (2026-05-19): the backend persists tool_calls + other
// per-message audit fields inside metadata_json. The frontend parses
// that string once at load time into structured fields so the chat
// rendering layer doesn't need to JSON.parse on every render.
export interface ChatToolCallSummary {
  iter: number
  name: string
  args: Record<string, any>
  result_chars: number
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  backend?: string
  model?: string
  latency_ms?: number
  cost_usd?: number
  attachments?: ChatStoredAttachment[]   // Slice 8
  // Slice 9.5 CP1: parsed from metadata_json. None of these are
  // required — historic rows from before persistence won't have them.
  tool_calls?: ChatToolCallSummary[]
  tool_iterations?: number
  history_turns_used?: number
  context_chars?: number
  // raw_metadata kept for /compress and debug — full original JSON.
  raw_metadata?: Record<string, any>
  // Slice 14.7 CP4: layer attribution. Empty/undefined on pre-14.7
  // rows; assistant rows from the router/overseer split carry these.
  answered_by?: 'router' | 'overseer' | ''
  escalation_reason?: string
}

export interface ChatHistoryResp {
  ok: boolean
  messages?: ChatMessage[]
  total?: number
  // Agent harness (2026-07-10): which thread the messages belong to
  // and which one is active Pi-side (sends always target the active).
  thread_id?: number
  active_thread_id?: number
}

// Agent harness (2026-07-10): chat threads + prompt library
export interface ChatThread {
  id: number
  title: string
  created_at: string
  updated_at: string
  message_count: number
  cost_usd: number
}

export interface ChatThreadsResp {
  ok: boolean
  threads?: ChatThread[]
  active_thread_id?: number
}

export interface ChatPrompt {
  id: number
  title: string
  body: string
  created_at: string
  updated_at: string
}

export interface ChatSendResp {
  ok: boolean
  reply?: string
  model?: string
  backend?: string
  latency_ms?: number
  cost_usd?: number
  error?: string
  attachments?: ChatStoredAttachment[]   // Slice 8
  // Slice 14.7 CP4: present on quick-chat responses; identifies which
  // layer answered + why if escalated.
  answered_by?: 'router' | 'overseer'
  escalation_reason?: string
  router_attempted?: boolean
}

export interface ChatUploadResp {
  ok: boolean
  attachments: ChatAttachmentRef[]
  rejected: { filename: string; size: number; error: string }[]
  counts: { uploaded: number; rejected: number }
}

// Local state for a file that the user has dropped/picked but hasn't
// sent yet. Lifecycle: queued → uploading → ready (with `ref`) → sent.
// On error, stays in the composer with `error` set so the user can
// retry or remove it.
export type PendingAttachmentStatus = 'queued' | 'uploading' | 'ready' | 'error'

export interface PendingAttachment {
  localId: string
  file: File
  filename: string
  size: number
  mime_type: string
  status: PendingAttachmentStatus
  previewUrl?: string                   // ObjectURL for images, revoked on remove
  ref?: ChatAttachmentRef               // populated once upload succeeds
  error?: string
}

// Mirror the Hub-side allowlist (hub/backend/routers/overseer.py).
export const CHAT_MAX_FILES = 10
export const CHAT_MAX_FILE_BYTES = 5 * 1024 * 1024
export const CHAT_ALLOWED_EXTS = new Set([
  '.txt', '.md', '.py', '.js', '.ts', '.tsx', '.jsx',
  '.json', '.yaml', '.yml', '.csv', '.log', '.html',
  '.css', '.sh', '.sql', '.toml', '.ini', '.env',
  '.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf',
])

export function fileExt(name: string): string {
  const ix = name.lastIndexOf('.')
  return ix < 0 ? '' : name.slice(ix).toLowerCase()
}

export function classifyKind(name: string, mime: string): ChatAttachmentKind {
  const ext = fileExt(name)
  if (mime.startsWith('image/') || ['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(ext)) return 'image'
  if (mime === 'application/pdf' || ext === '.pdf') return 'pdf'
  if (mime.startsWith('text/') || CHAT_ALLOWED_EXTS.has(ext)) return 'text'
  return 'other'
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// Slice 9.6 CP1 (2026-05-19): custom action button shape attached to
// notifications by overseer's emit_notification tool. kind drives the
// frontend's interaction model: predefined CRUD names execute server-
// side immediately; 'free_text' opens a textarea inline; 'yes_no'
// renders as a two-button pair; 'dispatch_sibling' is a single button
// that just logs the response (overseer reads + acts next tick).
export interface NotificationAction {
  label: string
  kind: string  // 'archive_project' | 'mark_dormant' | 'free_text' | 'yes_no' | 'dispatch_sibling' | custom
  payload?: Record<string, any>
}

export interface NotificationRow {
  id: number
  severity: 'info' | 'warn' | 'important'
  title: string
  body: string
  rule_name: string
  rule_key: string
  related_table: string
  related_id: string
  created_at: string
  dismissed_at: string | null
  // 3i CP1: per-rule action state
  snoozed_until: string | null
  archived_at: string | null
  // 9.6 CP1: actions_json parsed into the array form
  actions?: NotificationAction[]
  // Raw string from API (parsed in handler)
  actions_json?: string
}

export interface NotificationsResp {
  ok: boolean
  notifications?: NotificationRow[]
  unread_count?: number
}

export interface BudgetSnapshot {
  date: string
  cost_used_usd: number
  cost_max_usd: number
  cost_remaining_usd: number
  calls_used: number
  calls_max: number
  calls_remaining: number
  exhausted: boolean
}

export interface BudgetResp {
  ok: boolean
  budget?: BudgetSnapshot
}

// ── Slice 3f.5 types ──────────────────────────────────────────
// (Dialectic types removed 2026-07-10 with the tab sunset; the Pi
// keeps the frozen dialectic_open data, the UI no longer reads it.)

export interface JournalEntry {
  id: number
  written_at: string
  instance_id: string
  triggered_by: string
  body: string
  provisionality: 'high' | 'med' | 'low'
  model: string
  cost_usd: number
}

export interface JournalResp {
  ok: boolean
  entries?: JournalEntry[]
  total?: number
}

export interface BlindspotRow {
  id: number
  token?: string                 // 3g #2
  model_pattern: string
  topic_pattern: string
  direction: string
  confidence_adjustment: number
  body: string
  rationale: string
  confidence: string
  is_active: number
  apply_count: number
  last_applied_at: string | null
}

// ── Slice 3h: insight queue ─────────────────────────────────
export interface PendingInterpretation {
  id: number
  kind: 'theme' | 'pattern' | 'drift' | 'blindspot'
  title: string
  body: string
  confidence: string
  direction: string
  rationale: string
  proposed_by: string
  proposed_at: string
  source_kind: string
  source_project: string
  source_window_start: string | null
  source_window_end: string | null
  source_pointer_ids: string  // JSON array
  source_chat_message_id: number | null
  status: 'pending' | 'confirmed' | 'rejected' | 'edited' | 'superseded'
  reviewed_at: string | null
  reviewed_by: string
  review_note: string
  edit_title: string
  edit_body: string
  applied_table: string
  applied_id: number | null
  // 3i CP2: blindspot-kind specific fields
  bs_model_pattern?: string
  bs_topic_pattern?: string
  bs_confidence_adjustment?: number
}

export interface InsightScanRow {
  id: number
  scan_kind: string
  project: string
  window_start: string | null
  window_end: string | null
  gists_seen: number
  candidates_proposed: number
  candidates_deduped: number
  cost_usd: number
  triggered_by: string
  ok: number
  error: string
  scanned_at: string
}

export interface InsightScansResp {
  ok: boolean
  scans?: InsightScanRow[]
}

// 3i CP1: project classification table
export interface ProjectClassRow {
  project: string
  session_count: number
  avg_duration_minutes: number
  avg_messages: number
  total_messages: number
  last_seen: string | null
  treat_as: 'auto' | 'human' | 'automation' | 'ignore'
  manual_override: boolean
  classified_at: string | null
  classified_reason: string | null
  rollup_count: number
}

export interface ProjectsListResp {
  ok: boolean
  projects?: ProjectClassRow[]
  total?: number
}

export interface InsightPendingResp {
  ok: boolean
  interpretations?: PendingInterpretation[]
  counts?: { pending: number; confirmed: number; rejected: number; edited: number }
  error?: string
}

export interface InsightScanResp {
  ok: boolean
  project?: string
  gists_seen?: number
  candidates_proposed?: number
  candidates_deduped?: number
  cost_usd?: number
  scan_id?: number
  note?: string
  error?: string
}

// ── Helpers ───────────────────────────────────────────────────

export function fmtBytes(n?: number): string {
  if (!n || n < 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function fmtDuration(min?: number): string {
  if (!min || min <= 0) return '—'
  if (min < 60) return `${min}m`
  if (min < 60 * 24) return `${(min / 60).toFixed(1)}h`
  return `${(min / 60 / 24).toFixed(1)}d`
}

export function fmtRelative(iso?: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  const ms = Date.now() - t
  if (Number.isNaN(ms) || ms < 0) return iso.slice(0, 19)
  const sec = Math.round(ms / 1000)
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`
  return `${Math.round(sec / 86400)}d ago`
}

