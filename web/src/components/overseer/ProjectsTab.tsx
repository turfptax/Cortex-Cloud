/**
 * Slice 4 CP2: Projects tab.
 *
 * Consumes the rollup data shipped in CP1a (stats + tokens + top
 * files + models) plus the Sonnet narratives from CP1b. Renders one
 * card per project, sorted by Active hours by default, with a
 * collapsible details panel and per-project narrative regen button.
 *
 * Data flow:
 *   - GET /plugins/overseer/projects/summary?order_by=...&descending=...
 *   - POST /plugins/overseer/projects/summary/refresh-all (button)
 *   - POST /plugins/overseer/narrative/generate (per-card)
 *
 * Layout priority follows Tory's CP2 notes:
 *   1. Narrative front-and-center (always visible — no expand)
 *   2. Metrics row directly below: active hours · sessions · median
 *      · ratio · cost
 *   3. Collapsible secondary panel: top files, model mix, lifespan
 *   4. Per-card actions row: Regenerate narrative
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../../lib/api'
import { ProjectsPanel } from './panels/ProjectsPanel'
import { type ProjectClassRow, type ProjectsListResp } from './shared'

// ── Types matching the backend project_summaries shape ──────────

export interface ProjectRollup {
  project: string
  session_count: number
  total_messages: number
  total_user_messages: number
  total_assistant_messages: number
  tool_use_message_count: number
  total_minutes: number
  active_minutes_total: number
  avg_minutes_per_session: number
  median_minutes_per_session: number
  avg_active_minutes_per_session: number
  median_active_minutes_per_session: number
  total_tokens_input: number
  total_tokens_output: number
  total_tokens_cache_creation: number
  total_tokens_cache_read: number
  cost_usd_estimate: number
  cost_known_complete: number
  first_active_at: string | null
  last_active_at: string | null
  days_active_30: number
  days_active_90: number
  days_active_lifespan: number
  top_files: Array<{ path: string; hits: number }>
  models_used: Record<string, number>
  narrative: string
  narrative_updated_at: string | null
  narrative_session_count_at_update: number
  narrative_cost_usd: number
  stats_updated_at: string
}

interface ListResp {
  ok: boolean
  summaries?: ProjectRollup[]
  count?: number
  error?: string
}

interface GenerateResp {
  ok: boolean
  project?: string
  narrative?: string
  cost_usd?: number
  model?: string
  latency_ms?: number
  error?: string
}

interface RefreshAllResp {
  ok: boolean
  projects_total?: number
  refreshed?: number
  failed?: number
  error?: string
}

// ── Sort options ─────────────────────────────────────────────────

type SortKey =
  | 'active_minutes_total'
  | 'last_active_at'
  | 'cost_usd_estimate'
  | 'session_count'
  | 'project'

const SORT_LABEL: Record<SortKey, string> = {
  active_minutes_total: 'Active hours',
  last_active_at: 'Last active',
  cost_usd_estimate: 'Cost',
  session_count: 'Sessions',
  project: 'Name',
}

// Server's whitelist doesn't include active_minutes_total yet (CP1a
// allowed last_active_at, session_count, cost_usd_estimate,
// total_minutes, total_messages, first_active_at, stats_updated_at,
// project). We sort active_minutes client-side so we don't have to
// touch the backend whitelist for one sort key. For the others we
// pass to the server.
const SERVER_SORTABLE: ReadonlySet<SortKey> = new Set([
  'last_active_at', 'cost_usd_estimate', 'session_count', 'project',
])

// ── Helpers ──────────────────────────────────────────────────────

function fmtMinutes(min: number): string {
  if (!min || min < 1) return '—'
  if (min < 60) return `${Math.round(min)}m`
  const h = min / 60
  if (h < 10) return `${h.toFixed(1)}h`
  return `${Math.round(h)}h`
}

function fmtCost(c: number, complete: boolean): string {
  if (!c) return '$0'
  const flag = complete ? '' : '≥'
  if (c < 1) return `${flag}$${c.toFixed(2)}`
  if (c < 100) return `${flag}$${c.toFixed(2)}`
  return `${flag}$${Math.round(c).toLocaleString()}`
}

function fmtRelativeDays(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00Z')
  const ms = Date.now() - d.getTime()
  const days = Math.floor(ms / (24 * 60 * 60 * 1000))
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.round(days / 7)}w ago`
  if (days < 365) return `${Math.round(days / 30)}mo ago`
  return `${Math.round(days / 365)}y ago`
}

function ratioPct(active: number, wall: number): number | null {
  if (!wall) return null
  return Math.round((active / wall) * 1000) / 10
}

function shortPath(p: string): string {
  if (!p) return '?'
  const parts = p.replace(/\\/g, '/').replace(/\/$/, '').split('/')
  return parts.slice(-2).join('/')
}

// Activity-shape callout — surfaces in the card header.
// Icons make the shape readable at a glance when scanning a long
// list (Tory's dev.12 ask).
//   ⚡  quick-queries (UFOSINT pattern: ≥5 sessions, median <2min)
//   🌒  dormant spike (single session, last_active >60d)
//   ○   dormant (last_active >60d, multi-session)
//   ·   no measured active time (rare; backfilled but parser missed)
//
// `kind` lets the card decide layout/opacity (dormant variants get
// opacity-60).
type ShapeKind = 'quick-queries' | 'dormant-spike' | 'dormant'
                | 'no-active-time'

interface ActivityShape {
  kind: ShapeKind
  label: string
  icon: string
  tone: string
}

function activityShape(p: ProjectRollup): ActivityShape | null {
  const lastIso = p.last_active_at
  if (lastIso) {
    const days = (Date.now() - new Date(lastIso).getTime())
                 / (24 * 3600 * 1000)
    if (days >= 60 && p.session_count <= 1) {
      return {
        kind: 'dormant-spike',
        label: 'dormant spike',
        icon: '🌒',
        tone: 'text-amber-400/80',
      }
    }
    if (days >= 60) {
      return {
        kind: 'dormant',
        label: 'dormant',
        icon: '○',
        tone: 'text-text-muted',
      }
    }
  }
  if (p.session_count >= 5
      && p.median_active_minutes_per_session < 2) {
    return {
      kind: 'quick-queries',
      label: 'mostly quick queries',
      icon: '⚡',
      tone: 'text-text-muted',
    }
  }
  if (p.active_minutes_total === 0 && p.session_count > 0) {
    return {
      kind: 'no-active-time',
      label: 'no measured active time',
      icon: '·',
      tone: 'text-text-muted',
    }
  }
  return null
}

function isDormantShape(s: ActivityShape | null): boolean {
  return !!s && (s.kind === 'dormant' || s.kind === 'dormant-spike')
}

// ── Card ─────────────────────────────────────────────────────────

interface CardProps {
  rollup: ProjectRollup
  expanded: boolean
  toggleExpanded: () => void
  busy: boolean
  onRegenerate: () => void
}

function ProjectCard({ rollup, expanded, toggleExpanded, busy, onRegenerate }: CardProps) {
  const ratio = ratioPct(rollup.active_minutes_total, rollup.total_minutes)
  const shape = activityShape(rollup)
  const hasNarrative = rollup.narrative.trim().length > 0
  const dormant = isDormantShape(shape)
  const narrativeStale = (() => {
    if (!rollup.narrative_updated_at) return false
    const d = new Date(rollup.narrative_updated_at.includes('T')
      ? rollup.narrative_updated_at
      : rollup.narrative_updated_at + 'Z')
    const days = (Date.now() - d.getTime()) / (24 * 3600 * 1000)
    return days > 14
  })()

  // Dormant cards visually fade so active projects dominate the
  // sorted list. Hover restores full opacity so they're still
  // clickable without feeling broken.
  const cardOpacity = dormant
    ? 'opacity-60 hover:opacity-100 transition-opacity'
    : ''

  return (
    <article className={`bg-surface-secondary rounded-xl border border-border overflow-hidden ${cardOpacity}`}>
      {/* Header — project name is the strongest anchor at the top */}
      <header className="px-5 pt-4 pb-3 flex items-baseline gap-3 flex-wrap">
        <h3 className="text-base font-semibold text-text-primary truncate max-w-md">
          {rollup.project}
        </h3>
        {shape && (
          <span className={`inline-flex items-baseline gap-1 text-[10px] uppercase tracking-wider font-medium ${shape.tone}`}>
            <span className="text-[12px]">{shape.icon}</span>
            {shape.label}
          </span>
        )}
        <span className="ml-auto text-xs text-text-muted">
          last active {fmtRelativeDays(rollup.last_active_at)}
        </span>
      </header>

      {/* Narrative — front and center, primary text color */}
      <div className="px-5 pb-4">
        {hasNarrative ? (
          <NarrativeBlock text={rollup.narrative} />
        ) : (
          <p className="text-xs text-text-muted italic">
            No narrative yet. Click "Regenerate narrative" to ask the
            overseer to write one based on the current data, or wait
            for the loop tick to pick this project up.
          </p>
        )}
      </div>

      {/* Metrics row — Active hours is the hero stat. Others are
          compact tabular-nums next to it so the eye lands on Active
          first when scanning a long sorted list. */}
      <div className="px-5 py-3 border-t border-border bg-surface-tertiary/20 flex items-baseline gap-6 flex-wrap">
        <HeroMetric
          label="Active"
          value={fmtMinutes(rollup.active_minutes_total)}
          hint={ratio !== null ? `${ratio}% of wall-clock` : undefined}
        />
        <Metric label="Sessions" value={rollup.session_count.toLocaleString()} />
        <Metric label="Median active/sess"
                value={fmtMinutes(rollup.median_active_minutes_per_session)} />
        <Metric label="Cost"
                value={fmtCost(rollup.cost_usd_estimate, !!rollup.cost_known_complete)}
                hint={rollup.cost_known_complete ? undefined : 'lower bound — unpriced model in mix'} />
        <Metric label="Days active (90d)" value={`${rollup.days_active_90}`} />
        <button
          onClick={toggleExpanded}
          className="ml-auto text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer transition-colors"
        >
          {expanded ? 'Less ▴' : 'More ▾'}
        </button>
      </div>

      {/* Secondary panel — collapsed by default, grouped into
          Timeline (the "shape over time" cluster) + Compute (tokens,
          tool-use, model mix). Trimmed to the actionable fields. */}
      {expanded && (
        <div className="px-5 py-4 border-t border-border bg-surface/40 space-y-5">
          {rollup.top_files.length > 0 && (
            <Section title="Top files">
              <ul className="space-y-1">
                {rollup.top_files.slice(0, 8).map((f) => (
                  <li key={f.path}
                      className="flex items-baseline gap-3 text-xs">
                    <span className="text-text-muted/70 font-mono shrink-0 w-10 text-right">
                      {f.hits}×
                    </span>
                    <span className="text-text-secondary font-mono truncate"
                          title={f.path}>
                      {shortPath(f.path)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section title="Timeline">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-xs">
              <Mini label="Lifespan"
                    value={`${rollup.days_active_lifespan} days`} />
              <Mini label="First active"
                    value={rollup.first_active_at
                      ? fmtRelativeDays(rollup.first_active_at) : '—'} />
              <Mini label="Days active (30d)" value={`${rollup.days_active_30}`} />
              <Mini label="Wall-clock total" value={fmtMinutes(rollup.total_minutes)} />
            </div>
          </Section>

          <Section title="Compute">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-xs">
              <Mini label="Tool-use messages"
                    value={rollup.tool_use_message_count.toLocaleString()} />
              <Mini label="Cache reads"
                    value={rollup.total_tokens_cache_read.toLocaleString()} />
              <Mini label="Cache writes"
                    value={rollup.total_tokens_cache_creation.toLocaleString()} />
            </div>
            {Object.keys(rollup.models_used).length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(rollup.models_used)
                  .sort((a, b) => b[1] - a[1])
                  .map(([model, count]) => (
                    <span key={model}
                          className="px-2 py-0.5 rounded-full bg-text-muted/10 text-text-secondary text-[10px]">
                      {model} <span className="text-text-muted">×{count}</span>
                    </span>
                  ))}
              </div>
            )}
          </Section>

          {rollup.narrative_updated_at && (
            <p className="text-[10px] text-text-muted/70">
              Narrative regenerated {fmtRelativeDays(rollup.narrative_updated_at)}
              {' '}({rollup.narrative_session_count_at_update}{' '}
              sessions in DB at the time, {' '}
              ${rollup.narrative_cost_usd?.toFixed(4) || '0'} cost).
              {narrativeStale && (
                <span className="text-amber-400/80 ml-1">
                  · stale; loop will regen on next eligible tick
                </span>
              )}
            </p>
          )}
        </div>
      )}

      {/* Action row */}
      <footer className="px-5 py-2 border-t border-border bg-surface-tertiary/30 flex items-center gap-3">
        <button
          onClick={onRegenerate}
          disabled={busy}
          className="px-3 py-1 rounded-md text-[11px] font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer disabled:opacity-40"
        >
          {hasNarrative ? 'Regenerate narrative' : 'Generate narrative'}
        </button>
      </footer>
    </article>
  )
}

// ── Sub-components ───────────────────────────────────────────────

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col" title={hint}>
      <span className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span className="text-sm font-medium text-text-primary tabular-nums">
        {value}
      </span>
    </div>
  )
}

// Hero Active metric — visually dominant in the row so the eye lands
// on it first when scanning a long sorted list. text-2xl bold;
// optional ratio chip rendered as muted small-caps next to the value.
function HeroMetric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col" title={hint}>
      <span className="text-[10px] uppercase tracking-wider text-accent-hover/80 font-medium">
        {label}
      </span>
      <span className="flex items-baseline gap-2">
        <span className="text-2xl font-bold text-text-primary tabular-nums leading-none">
          {value}
        </span>
        {hint && (
          <span className="text-[10px] uppercase tracking-wider text-text-muted">
            {hint}
          </span>
        )}
      </span>
    </div>
  )
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span className="text-text-secondary tabular-nums">{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">
        {title}
      </h4>
      {children}
    </div>
  )
}

// Renders the narrative paragraphs + the trailing "## Open questions
// still live" header (if present). text-primary because the
// narrative is the hero content, not a caption.
//
// Default (collapsed): shows only the FIRST paragraph + a "Read
// full ▾" toggle. With 47 cards × 3 paragraphs each, full-expanded
// would be a 15K-pixel scroll wall. The expand toggle keeps the
// list readable while letting the user dig in.
function NarrativeBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const QUESTION_HEADER = /\n## Open questions still live\n/
  const splitOnHeader = text.split(QUESTION_HEADER)
  const body = splitOnHeader[0].trim()
  const questions = splitOnHeader.length > 1
    ? splitOnHeader[1].trim() : ""

  // First paragraph = text up to first double-newline. If the
  // narrative has no paragraph breaks (rare but possible), the whole
  // body is the "preview" and there's nothing more to expand.
  const paragraphs = body.split(/\n\n+/).map((p) => p.trim()).filter(Boolean)
  const hasMore = paragraphs.length > 1
  const visiblePreview = paragraphs[0] || body
  const visibleBody = expanded ? body : visiblePreview

  return (
    <>
      <div className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
        {visibleBody}
      </div>
      {hasMore && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-[11px] uppercase tracking-wider text-accent-hover hover:text-accent cursor-pointer"
        >
          {expanded ? 'Show less ▴' : 'Read full ▾'}
        </button>
      )}
      {questions && (
        <div className="mt-3 pl-3 border-l-2 border-accent/40">
          <h4 className="text-[10px] uppercase tracking-wider text-accent-hover/70 mb-1">
            Open questions still live
          </h4>
          <div className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">
            {questions}
          </div>
        </div>
      )}
    </>
  )
}

// ── Main panel ───────────────────────────────────────────────────

export function ProjectsTab() {
  const [rollups, setRollups] = useState<ProjectRollup[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('active_minutes_total')
  const [descending, setDescending] = useState(true)
  const [expandedSet, setExpandedSet] = useState<Set<string>>(new Set())
  // Active-only filter: hides cards whose activityShape is dormant
  // or dormant-spike. Useful when the user just wants to see what
  // they're currently working on.
  const [activeOnly, setActiveOnly] = useState(false)

  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true)
    setError(null)
    try {
      // Use a server-side sort when supported, otherwise pull and
      // sort client-side below. last_active_at is a safe always-
      // sortable default for the API call.
      const serverSort = SERVER_SORTABLE.has(sortKey)
        ? sortKey
        : 'last_active_at'
      const params = new URLSearchParams({
        order_by: serverSort,
        descending: descending ? '1' : '0',
      })
      const r = await apiFetch<ListResp>(
        `/overseer/projects/summary?${params}`,
      )
      setRollups(r.summaries || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [sortKey, descending])

  // Auto-refresh on first mount.
  useEffect(() => {
    if (rollups === null) {
      void refresh()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Re-fetch when sort key changes (server may handle the sort, or
  // we resort client-side below if it doesn't).
  useEffect(() => {
    if (rollups !== null) {
      void refresh({ silent: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortKey, descending])

  // Client-side sort fallback for keys the server doesn't whitelist.
  const sortedRollups = useMemo(() => {
    if (!rollups) return []
    let out = SERVER_SORTABLE.has(sortKey)
      ? [...rollups]
      : [...rollups].sort((a, b) => {
          const av = (a as any)[sortKey] ?? 0
          const bv = (b as any)[sortKey] ?? 0
          return descending ? bv - av : av - bv
        })
    if (activeOnly) {
      out = out.filter((p) => !isDormantShape(activityShape(p)))
    }
    return out
  }, [rollups, sortKey, descending, activeOnly])

  const totalCount = rollups?.length ?? 0
  const visibleCount = sortedRollups.length
  const hiddenByFilter = totalCount - visibleCount

  const handleRebuildStats = async () => {
    setBusy('Rebuilding stats…')
    setError(null)
    try {
      const r = await apiFetch<RefreshAllResp>(
        '/overseer/projects/summary/refresh-all',
        { method: 'POST', body: JSON.stringify({}) },
      )
      if (!r.ok) {
        setError(r.error || 'rebuild failed')
      }
      await refresh({ silent: true })
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy(null)
    }
  }

  const handleRegenerate = useCallback(async (project: string) => {
    setBusy(`Regenerating narrative for ${project}…`)
    setError(null)
    try {
      const r = await apiFetch<GenerateResp>(
        '/overseer/narrative/generate',
        {
          method: 'POST',
          body: JSON.stringify({ project, force: true }),
        },
      )
      if (!r.ok) {
        setError(r.error || 'narrative generation failed')
        return
      }
      // Patch in the new narrative locally so we don't have to
      // refetch the whole list.
      setRollups((prev) => prev?.map((p) =>
        p.project === project
          ? { ...p, narrative: r.narrative || p.narrative,
              narrative_updated_at: new Date().toISOString(),
              narrative_cost_usd: r.cost_usd ?? p.narrative_cost_usd,
              narrative_session_count_at_update: p.session_count }
          : p
      ) ?? null)
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy(null)
    }
  }, [])

  const toggleExpanded = useCallback((project: string) => {
    setExpandedSet((prev) => {
      const next = new Set(prev)
      if (next.has(project)) next.delete(project)
      else next.add(project)
      return next
    })
  }, [])

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      {/* Top action bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-base font-semibold text-text-primary">
          Projects
        </h2>
        <span className="text-xs text-text-muted">
          {activeOnly && hiddenByFilter > 0
            ? `${visibleCount} active (${hiddenByFilter} dormant hidden)`
            : `${totalCount} project${totalCount === 1 ? '' : 's'}`}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {/* Active-only / All toggle. Pill-style segmented control —
              small enough to live next to the sort selector without
              competing for attention. */}
          <div className="flex bg-surface-tertiary rounded-md p-0.5 border border-border">
            <button
              onClick={() => setActiveOnly(true)}
              className={`px-2.5 py-0.5 rounded text-[11px] font-medium cursor-pointer transition-colors ${
                activeOnly
                  ? 'bg-accent/30 text-accent-hover'
                  : 'text-text-muted hover:text-text-primary'
              }`}
            >
              Active only
            </button>
            <button
              onClick={() => setActiveOnly(false)}
              className={`px-2.5 py-0.5 rounded text-[11px] font-medium cursor-pointer transition-colors ${
                !activeOnly
                  ? 'bg-accent/30 text-accent-hover'
                  : 'text-text-muted hover:text-text-primary'
              }`}
            >
              All
            </button>
          </div>
          <label className="text-[10px] uppercase tracking-wider text-text-muted ml-1">
            Sort
          </label>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="bg-surface-tertiary text-text-primary text-xs rounded-md px-2 py-1 border border-border focus:outline-none focus:border-accent cursor-pointer"
          >
            {(Object.entries(SORT_LABEL) as [SortKey, string][]).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          <button
            onClick={() => setDescending((v) => !v)}
            className="px-2 py-1 rounded-md text-xs bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
            title={descending ? 'Descending' : 'Ascending'}
          >
            {descending ? '↓' : '↑'}
          </button>
          <button
            onClick={handleRebuildStats}
            disabled={!!busy || loading}
            className="px-3 py-1 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-40"
          >
            Rebuild stats
          </button>
          <button
            onClick={() => refresh()}
            disabled={!!busy || loading}
            className="px-3 py-1 rounded-md text-xs font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer disabled:opacity-40"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Status bar */}
      {(busy || error) && (
        <div className="flex items-center gap-3 text-xs">
          {busy && <span className="text-text-muted">{busy}</span>}
          {error && (
            <span className="text-red-400">{error}</span>
          )}
        </div>
      )}

      {/* Body */}
      {loading && rollups === null && (
        <div className="text-sm text-text-muted py-12 text-center">
          Loading project rollups…
        </div>
      )}

      {!loading && sortedRollups.length === 0 && (
        <div className="text-sm text-text-muted py-12 text-center">
          No project rollups yet. If you've already run the backfill
          script, hit "Rebuild stats" to populate the table.
        </div>
      )}

      {sortedRollups.map((p) => (
        <ProjectCard
          key={p.project}
          rollup={p}
          expanded={expandedSet.has(p.project)}
          toggleExpanded={() => toggleExpanded(p.project)}
          busy={!!busy}
          onRegenerate={() => handleRegenerate(p.project)}
        />
      ))}

      <ClassificationSection />
    </div>
  )
}

// ── Classification (IA overhaul 2026-07-10) ──────────────────────
// The old standalone Classify tab, folded in here as a collapsed
// section: per-project treat-as overrides (human / automation /
// ignore) live next to the projects they classify. Self-contained
// state so ProjectsTab stays independent of OverseerPage.

function ClassificationSection() {
  const [open, setOpen] = useState(false)
  const [projects, setProjects] = useState<ProjectClassRow[]>([])
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [lastAction, setLastAction] = useState('')

  const refresh = useCallback(async () => {
    try {
      const r = await apiFetch<ProjectsListResp>('/overseer/projects')
      setProjects(r.projects || [])
    } catch (e: any) {
      setError(`Classification refresh failed: ${e?.message || e}`)
    }
  }, [])

  useEffect(() => {
    if (open && projects.length === 0) refresh()
  }, [open, projects.length, refresh])

  const handleSetClass = async (
    project: string,
    treat_as: 'auto' | 'human' | 'automation' | 'ignore',
  ) => {
    try {
      await apiFetch<any>('/overseer/projects/setting', {
        method: 'POST',
        body: JSON.stringify({ project, treat_as }),
      })
      await refresh()
      setLastAction(
        `Set ${project} → ${treat_as}` +
        (treat_as === 'auto' ? ' (cleared override)' : ''),
      )
    } catch (e: any) {
      setError(`Project update failed: ${e?.message || e}`)
    }
  }

  const handleClassifyNow = async () => {
    setBusy('Classifying…')
    try {
      const r = await apiFetch<{ ok: boolean; changes?: any }>(
        '/overseer/projects/classify',
        { method: 'POST' },
      )
      if (r.ok) {
        const changed = (r.changes && r.changes.changed) || 0
        setLastAction(`Classifier ran. ${changed} project(s) changed.`)
      }
      await refresh()
    } catch (e: any) {
      setError(`Classify-now failed: ${e?.message || e}`)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="border-t border-border pt-4 mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 text-sm font-medium text-text-secondary hover:text-text-primary cursor-pointer"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>Classification</span>
        <span className="text-xs text-text-muted font-normal">
          per-project treat-as: human / automation / ignore
        </span>
      </button>
      {(lastAction || error) && open && (
        <div className="mt-2 text-xs">
          {lastAction && <span className="text-success">{lastAction}</span>}
          {error && <span className="text-red-400 ml-2">{error}</span>}
        </div>
      )}
      {open && (
        <div className="mt-3">
          <ProjectsPanel
            projects={projects}
            filter={filter}
            setFilter={setFilter}
            onSetClass={handleSetClass}
            onClassifyNow={handleClassifyNow}
            onRefresh={refresh}
            busy={busy}
          />
        </div>
      )}
    </div>
  )
}
