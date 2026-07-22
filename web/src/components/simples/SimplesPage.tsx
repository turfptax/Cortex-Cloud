import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../../lib/api'
import { fmtRelative } from '../overseer/shared'

// Simples on the desktop (2026-07-11): a READ-ONLY mirror of the
// phone's liquid planner with Day / Week / Month views. The phone
// pushes a snapshot blob on every home sync; editing (goals, reflow,
// block states) stays on the phone where the reflow engine lives.
//
// Visual language mirrors the phone: blocks are boats, downtime is
// water (a recessive full-width wash, never a loud color). Anchors
// are external calendar events and render OUTLINED (dashed) so their
// identity is carried by shape, not hue alone. Kind hues follow the
// phone's entity colors, darkened to pass the dark-surface palette
// checks (lightness band, CVD separation, contrast) — validated with
// the dataviz six-check script on 2026-07-11.

interface Goal {
  id: number
  title: string
  project_tag: string
  target_hours: number
  period_start: string
  period_end: string
  status: string
}

interface Block {
  id: number
  day: string
  start_min: number
  end_min: number
  kind: string
  title: string
  goal_id: number | null
  state: string
  pinned: number
}

interface Snapshot {
  goals: Goal[]
  blocks: Block[]
  from: string
  to: string
  received_at: string
}

type ViewMode = 'day' | 'week' | 'month' | 'year'

// Per-day corpus aggregates from GET /overseer/day/heat.
interface DayHeat {
  s?: number   // AI-session minutes
  sc?: number  // AI-session count
  t?: number   // logged time-entry minutes
  z?: number   // hours slept
  p?: number   // steps
  a?: number   // active-zone minutes
}

// GET /overseer/day: everything the corpus holds about one local day.
interface DayDetail {
  ok: boolean
  date: string
  sessions: {
    id: string
    source: string
    project: string
    started_at?: string
    local_started_at?: string
    duration_minutes: number
    message_count: number
    tool_use_count: number
    sensitivity?: string | null
    redacted: boolean
    gist: string
  }[]
  session_minutes: number
  time_entries: {
    project_tag: string
    activity_type: string
    description: string
    local_started_at?: string
    duration_minutes: number
  }[]
  logged_minutes: number
  health: Record<string, number>
  journal: { text: string; entry_type: string; local_created_at?: string }[]
  narrative: string
}

// Entity colors (validated categorical set; work + downtime are
// deliberately recessive neutrals and sit outside the loud set).
const KIND_COLOR: Record<string, string> = {
  goal: '#7c5cff',
  chore: '#d97706',
  adl: '#c026d3',
  anchor: '#b45309',
  work: '#8b949e',
  downtime: '#3d4a5c',
}
const KIND_LABEL: Record<string, string> = {
  goal: 'Goal',
  work: 'Work',
  adl: 'ADL',
  chore: 'Chore',
  anchor: 'Calendar',
  downtime: 'Downtime',
}
const LEGEND_ORDER = ['goal', 'work', 'adl', 'chore', 'anchor', 'downtime']

const kindColor = (k: string) => KIND_COLOR[k] || '#8b949e'

// ── corpus visualization invariants (UX panel spec, 2026-07-12) ─────
// - Sequential activity = the one purple #7c5cff alpha ramp, cell
//   body only.
// - Sleep = the one permitted second hue, #35a99b (validated against
//   the dark surface: lightness band, chroma, deutan dE 74, contrast
//   all pass). Sleep is physiologically distinct and non-additive; a
//   bright cell must never be ambiguous between "worked a lot" and
//   "slept a lot". Identity is position-locked: teal only ever
//   appears as the BOTTOM band of a day cell or a labeled row; hue is
//   never load-bearing.
// - Sleep is LENGTH-encoded at fixed alpha everywhere (10h = full
//   width). One rule, all views.
// - Weekend identity = position (detached band / seam) + label
//   weight + a 3% white wash. Never a hue.
// - Missing data: activity missing inside the observed span = 0 (a
//   quiet Saturday is real); sleep/steps missing = absent, excluded
//   (instrument absence is not insomnia). Both rules live in
//   deriveScope.
// - No streaks, scores, deltas, targets, or coaching copy anywhere.
const SLEEP_TEAL = '#35a99b'
const TXT_MUTED = '#64748b'      // --color-text-muted, for SVG fills
const TXT_SECONDARY = '#94a3b8'  // --color-text-secondary, for SVG fills
const sleepFrac = (z: number) => Math.min(1, z / 10)
const isWeekend = (d: string) => {
  const g = new Date(d + 'T12:00:00').getDay()
  return g === 0 || g === 6
}
const activityShade = (min: number) =>
  `rgba(124,92,255,${Math.min(0.9, 0.15 + (min / 360) * 0.75).toFixed(2)})`
const monthShade = (min: number) =>
  min <= 0
    ? 'transparent'
    : `rgba(124,92,255,${Math.min(0.42, 0.08 + (min / 480) * 0.34).toFixed(2)})`

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return 0
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

// Everything RhythmCard needs about a scoped run of dates.
interface Scope {
  dates: string[]
  activeByDow: number[][]  // 7 arrays (Mon..Sun) of daily active MINUTES
  sleepByDow: number[][]   // 7 arrays of hours slept; missing = excluded
  stepsByDow: number[][]   // 7 arrays of steps; missing = excluded
  counts: { sessions: number; logged: number; nights: number }
}

function deriveScope(
  heatFor: (d: string) => DayHeat | undefined, dates: string[], today: string,
): Scope {
  const activeByDow: number[][] = Array.from({ length: 7 }, () => [])
  const sleepByDow: number[][] = Array.from({ length: 7 }, () => [])
  const stepsByDow: number[][] = Array.from({ length: 7 }, () => [])
  const counts = { sessions: 0, logged: 0, nights: 0 }
  // Observed span for ACTIVITY: first..last date with any data,
  // clamped to today; inside it a missing day counts as zero (a
  // quiet Saturday is real data). Sleep/steps missing = absent (the
  // tracker was off, not the person sleepless). The asymmetry is
  // deliberate.
  let first = '', last = ''
  for (const d of dates) {
    if (heatFor(d)) { if (!first) first = d; last = d }
  }
  if (last > today) last = today
  for (const d of dates) {
    const dow = (new Date(d + 'T12:00:00').getDay() + 6) % 7 // Mon=0
    const h = heatFor(d)
    if (first && d >= first && d <= last) {
      activeByDow[dow].push((h?.s || 0) + (h?.t || 0))
    }
    if (h?.z != null) { sleepByDow[dow].push(h.z); counts.nights++ }
    if (h?.p != null) stepsByDow[dow].push(h.p)
    if (h?.sc) counts.sessions++
    if (h?.t) counts.logged++
  }
  return { dates, activeByDow, sleepByDow, stepsByDow, counts }
}

// ── date helpers (local-day strings, matching the phone) ───────────

function localDay(offsetDays: number, base?: string): string {
  const d = base ? new Date(base + 'T12:00:00') : new Date()
  d.setDate(d.getDate() + offsetDays)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function weekStart(day: string): string {
  const d = new Date(day + 'T12:00:00')
  const dow = (d.getDay() + 6) % 7 // Monday = 0
  return localDay(-dow, day)
}

function monthDates(anchorDay: string): string[] {
  const month = anchorDay.slice(0, 7)
  const first = month + '-01'
  const out: string[] = []
  for (let i = 0; i < 31; i++) {
    const d = localDay(i, first)
    if (d.slice(0, 7) !== month) break
    out.push(d)
  }
  return out
}

function yearDates(year: number): string[] {
  const out: string[] = []
  const p = (n: number) => String(n).padStart(2, '0')
  for (let mi = 0; mi < 12; mi++) {
    const daysIn = new Date(year, mi + 1, 0).getDate()
    for (let di = 1; di <= daysIn; di++) out.push(`${year}-${p(mi + 1)}-${p(di)}`)
  }
  return out
}

function fmtMin(m: number): string {
  const h = Math.floor(m / 60)
  const mm = m % 60
  const h12 = ((h + 11) % 12) + 1
  return `${h12}${mm ? ':' + String(mm).padStart(2, '0') : ''}${h < 12 ? 'am' : 'pm'}`
}

function dayTitle(day: string, today: string): string {
  if (day === today) return 'Today'
  if (day === localDay(1, today)) return 'Tomorrow'
  if (day === localDay(-1, today)) return 'Yesterday'
  return new Date(day + 'T12:00:00').toLocaleDateString(undefined, {
    weekday: 'long', month: 'short', day: 'numeric',
  })
}

// ── lane layout: overlapping non-downtime blocks share the width ───

interface Laned extends Block {
  lane: number
  lanes: number
}

function assignLanes(blocks: Block[]): Laned[] {
  const sorted = [...blocks].sort(
    (a, b) => a.start_min - b.start_min || b.end_min - a.end_min)
  const laneEnds: number[] = []
  const out: Laned[] = []
  for (const b of sorted) {
    let lane = laneEnds.findIndex((end) => end <= b.start_min)
    if (lane === -1) {
      lane = laneEnds.length
      laneEnds.push(b.end_min)
    } else {
      laneEnds[lane] = b.end_min
    }
    out.push({ ...b, lane, lanes: 1 })
  }
  // Lane count per overlap cluster so widths only shrink where needed.
  for (const b of out) {
    let overlapMax = b.lane
    for (const o of out) {
      if (o.start_min < b.end_min && o.end_min > b.start_min) {
        overlapMax = Math.max(overlapMax, o.lane)
      }
    }
    b.lanes = overlapMax + 1
  }
  return out
}

// ── page ────────────────────────────────────────────────────────────

export function SimplesPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const today = localDay(0)
  const [view, setView] = useState<ViewMode>(() => {
    const v = localStorage.getItem('cortex.simples.view')
    return v === 'week' || v === 'month' || v === 'year' ? v : 'day'
  })
  const [anchorDay, setAnchorDay] = useState<string>(today)

  // Year view: per-day corpus aggregates from the Pi (AI-session
  // minutes, logged time, sleep, steps) for ANY year the corpus
  // covers. This is the permanent-memory view: the desktop sees the
  // whole corpus, not just the phone's mirrored window.
  // Failures are tracked separately and NEVER cached as data: a Pi
  // WiFi flap must not render a permanently blank year (the proxy
  // wraps Pi-down as HTTP 200 + ok:false, so r.ok is the signal).
  const [heatYears, setHeatYears] = useState<Record<number, Record<string, DayHeat>>>({})
  const [heatFail, setHeatFail] = useState<Record<number, boolean>>({})
  const anchorYear = Number(anchorDay.slice(0, 4))
  useEffect(() => {
    if (view === 'day') return
    // Week and month grids can straddle a year boundary; fetch every
    // year the visible cells touch.
    const needed = new Set<number>()
    if (view === 'year') needed.add(anchorYear)
    else if (view === 'week') {
      const start = weekStart(anchorDay)
      for (let i = 0; i < 7; i++) needed.add(Number(localDay(i, start).slice(0, 4)))
    } else {
      const gridStart = weekStart(anchorDay.slice(0, 8) + '01')
      needed.add(Number(gridStart.slice(0, 4)))
      needed.add(Number(localDay(41, gridStart).slice(0, 4)))
    }
    for (const y of needed) {
      if (heatYears[y] || heatFail[y]) continue
      apiFetch<{ ok: boolean; days?: Record<string, DayHeat> }>(
        `/overseer/day/heat?year=${y}`,
      )
        .then((r) => {
          if (r && r.ok) setHeatYears((h) => ({ ...h, [y]: r.days || {} }))
          else setHeatFail((f) => ({ ...f, [y]: true }))
        })
        .catch(() => setHeatFail((f) => ({ ...f, [y]: true })))
    }
  }, [view, anchorDay, anchorYear, heatYears, heatFail])
  const retryHeat = (y: number) =>
    setHeatFail((f) => { const n = { ...f }; delete n[y]; return n })
  const heatFor = (d: string): DayHeat | undefined =>
    heatYears[Number(d.slice(0, 4))]?.[d]

  useEffect(() => {
    localStorage.setItem('cortex.simples.view', view)
  }, [view])

  useEffect(() => {
    apiFetch<{ ok: boolean; snapshot?: Snapshot | null; error?: string }>(
      '/overseer/simples/snapshot',
    )
      .then((r) => {
        if (!r.ok) setError(r.error || 'snapshot fetch failed')
        else setSnap(r.snapshot ?? null)
      })
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoaded(true))
  }, [])

  const byDay = useMemo(() => {
    const m = new Map<string, Block[]>()
    for (const b of snap?.blocks || []) {
      const arr = m.get(b.day) || []
      arr.push(b)
      m.set(b.day, arr)
    }
    return m
  }, [snap])

  const step = (dir: number) => {
    const n = view === 'day' ? 1 : view === 'week' ? 7 : 0
    if (view === 'month') {
      const d = new Date(anchorDay + 'T12:00:00')
      d.setMonth(d.getMonth() + dir, 15)
      const p = (x: number) => String(x).padStart(2, '0')
      setAnchorDay(`${d.getFullYear()}-${p(d.getMonth() + 1)}-15`)
    } else if (view === 'year') {
      const y = Number(anchorDay.slice(0, 4)) + dir
      setAnchorDay(`${y}-07-01`)
    } else {
      setAnchorDay(localDay(dir * n, anchorDay))
    }
  }

  if (!loaded) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-text-muted animate-pulse">Loading planner…</div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex-1 p-8 text-sm text-red-400">
        Simples mirror unavailable: {error}
      </div>
    )
  }
  if (!snap || (snap.goals.length === 0 && snap.blocks.length === 0)) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
        <div className="text-4xl">📅</div>
        <div className="text-sm text-text-secondary max-w-md text-center">
          No plan mirrored yet. Simples lives on your phone — open the app
          on home WiFi and sync, and the plan appears here.
        </div>
      </div>
    )
  }

  const syncedAgo = fmtRelative(
    snap.received_at.includes('T')
      ? snap.received_at
      : snap.received_at.replace(' ', 'T') + 'Z')
  const staleSync =
    Date.now() - new Date(
      snap.received_at.replace(' ', 'T') + 'Z').getTime() > 86_400_000

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-4">
        {/* Header: view toggle + date nav + sync badge */}
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-lg font-semibold text-text-primary mr-1">Simples</h2>
          <div className="flex rounded-md border border-border overflow-hidden">
            {(['day', 'week', 'month', 'year'] as ViewMode[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1 text-xs font-medium cursor-pointer capitalize ${
                  view === v
                    ? 'bg-accent/20 text-accent'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {v}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <NavBtn label="‹" onClick={() => step(-1)} />
            <button
              onClick={() => setAnchorDay(today)}
              className="px-2.5 py-1 rounded text-xs text-text-secondary hover:text-text-primary border border-border cursor-pointer"
            >
              {view === 'day' ? 'Today'
                : view === 'week' ? 'This week'
                : view === 'month' ? 'This month'
                : 'This year'}
            </button>
            <NavBtn label="›" onClick={() => step(1)} />
          </div>
          <div className="ml-auto text-xs text-text-muted">
            {staleSync ? '⚠ ' : ''}phone synced {syncedAgo}
          </div>
        </div>

        <GoalsCard goals={snap.goals} blocks={snap.blocks} />

        {view === 'day' && (
          <>
            <DayView
              day={anchorDay}
              today={today}
              blocks={byDay.get(anchorDay) || []}
              inWindow={anchorDay >= snap.from && anchorDay <= snap.to}
            />
            <DayCorpusCard date={anchorDay} />
          </>
        )}
        {view === 'week' && (
          <WeekView
            start={weekStart(anchorDay)}
            today={today}
            byDay={byDay}
            heatFor={heatFor}
            onPickDay={(d) => { setAnchorDay(d); setView('day') }}
          />
        )}
        {view === 'month' && (
          <>
            <MonthView
              anchorDay={anchorDay}
              today={today}
              byDay={byDay}
              heatFor={heatFor}
              onPickDay={(d) => { setAnchorDay(d); setView('day') }}
            />
            <RhythmCard
              dates={monthDates(anchorDay)}
              label={new Date(anchorDay.slice(0, 8) + '01T12:00:00')
                .toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
              today={today}
              heatFor={heatFor}
            />
          </>
        )}
        {view === 'year' && (
          <>
            <YearView
              year={anchorYear}
              today={today}
              heat={heatYears[anchorYear] || {}}
              loading={!heatYears[anchorYear] && !heatFail[anchorYear]}
              error={!!heatFail[anchorYear]}
              onRetry={() => retryHeat(anchorYear)}
              onPickDay={(d) => { setAnchorDay(d); setView('day') }}
            />
            <RhythmCard
              dates={yearDates(anchorYear)}
              label={String(anchorYear)}
              today={today}
              heatFor={heatFor}
            />
          </>
        )}

        {/* Legend: identity is never color-alone (labels + dots). */}
        <div className="flex items-center gap-4 flex-wrap text-[11px] text-text-muted">
          {LEGEND_ORDER.map((k) => (
            <span key={k} className="flex items-center gap-1.5">
              <span
                className="w-2.5 h-2.5 rounded-sm"
                style={
                  k === 'anchor'
                    ? { border: `1.5px dashed ${kindColor(k)}` }
                    : { background: kindColor(k) }
                }
              />
              {KIND_LABEL[k]}
            </span>
          ))}
          <span className="flex items-center gap-1.5">✓ done</span>
          <span className="flex items-center gap-1.5 line-through">missed</span>
          {(view === 'month' || view === 'year') && (
            <>
              <span className="flex items-center gap-1.5">
                <span
                  className="w-2.5 h-2.5 rounded-sm"
                  style={{ background: 'rgba(124,92,255,0.75)' }}
                />
                activity (AI + logged)
              </span>
              <span className="flex items-center gap-1.5">
                <span
                  className="w-2.5 h-1 rounded"
                  style={{ background: SLEEP_TEAL }}
                />
                sleep
              </span>
            </>
          )}
          <span className="ml-auto">
            Read-only mirror — edit on the phone (Simples tab or by voice).
          </span>
        </div>
      </div>
    </div>
  )
}

function NavBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-7 h-7 rounded border border-border text-text-secondary hover:text-text-primary cursor-pointer text-sm"
      aria-label={label === '‹' ? 'Previous' : 'Next'}
    >
      {label}
    </button>
  )
}

// ── goals ───────────────────────────────────────────────────────────

function GoalsCard({ goals, blocks }: { goals: Goal[]; blocks: Block[] }) {
  const active = goals.filter((g) => g.status === 'active')
  if (active.length === 0) return null
  const doneMin = new Map<number, number>()
  for (const b of blocks) {
    if (b.state === 'done' && b.goal_id) {
      doneMin.set(b.goal_id,
        (doneMin.get(b.goal_id) || 0) + (b.end_min - b.start_min))
    }
  }
  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-3">
        {active.map((g) => {
          const done = (doneMin.get(g.id) || 0) / 60
          const pct = g.target_hours > 0
            ? Math.min(100, Math.round((done / g.target_hours) * 100))
            : 0
          return (
            <div key={g.id}>
              <div className="flex justify-between text-xs mb-1 gap-2">
                <span className="font-medium text-text-primary truncate">
                  {g.title}
                  {g.project_tag ? (
                    <span className="text-text-muted"> · {g.project_tag}</span>
                  ) : null}
                </span>
                <span className="text-text-muted shrink-0">
                  {done.toFixed(1)} / {g.target_hours}h
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-surface-tertiary overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${pct}%`, background: KIND_COLOR.goal }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── day view: the timeline ──────────────────────────────────────────

const PX_PER_MIN = 1.05

function DayView({ day, today, blocks, inWindow }: {
  day: string
  today: string
  blocks: Block[]
  inWindow: boolean
}) {
  const water = blocks.filter((b) => b.kind === 'downtime')
  const boats = assignLanes(blocks.filter((b) => b.kind !== 'downtime'))
  const all = blocks
  const startMin = Math.min(6 * 60, ...all.map((b) => b.start_min))
  const endMin = Math.max(22 * 60, ...all.map((b) => b.end_min))
  const height = (endMin - startMin) * PX_PER_MIN
  const y = (m: number) => (m - startMin) * PX_PER_MIN
  const hours: number[] = []
  for (let h = Math.ceil(startMin / 60); h * 60 <= endMin; h++) hours.push(h)
  const now = new Date()
  const nowMin = now.getHours() * 60 + now.getMinutes()
  const showNow = day === today && nowMin >= startMin && nowMin <= endMin

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">
        {dayTitle(day, today)}
        <span className="text-xs font-normal text-text-muted ml-2">{day}</span>
      </h3>
      {blocks.length === 0 ? (
        <div className="text-xs text-text-muted py-8 text-center">
          {inWindow
            ? 'No blocks planned this day.'
            : 'Outside the mirrored window (the phone mirrors -7 to +14 days).'}
        </div>
      ) : (
        <div className="relative" style={{ height }}>
          {/* hour grid (recessive) */}
          {hours.map((h) => (
            <div
              key={h}
              className="absolute left-0 right-0 border-t border-border/30"
              style={{ top: y(h * 60) }}
            >
              <span className="absolute -top-2 left-0 text-[10px] text-text-muted w-11">
                {fmtMin(h * 60)}
              </span>
            </div>
          ))}
          {/* downtime = water: recessive full-width washes */}
          {water.map((b) => (
            <div
              key={b.id}
              title={`${b.title || 'Downtime'} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)}`}
              className="absolute rounded"
              style={{
                top: y(b.start_min),
                height: Math.max(6, (b.end_min - b.start_min) * PX_PER_MIN - 2),
                left: 52,
                right: 0,
                background: 'rgba(61,74,92,0.16)',
              }}
            />
          ))}
          {/* boats: laned blocks */}
          {boats.map((b) => {
            const c = kindColor(b.kind)
            const isAnchor = b.kind === 'anchor'
            const missed = b.state === 'missed' || b.state === 'skipped'
            const done = b.state === 'done'
            const laneW = 100 / b.lanes
            const h = Math.max(16, (b.end_min - b.start_min) * PX_PER_MIN - 2)
            return (
              <div
                key={b.id}
                title={`${b.title || b.kind} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)} · ${b.state}`}
                className={`absolute rounded-md px-2 py-0.5 overflow-hidden text-xs ${
                  missed ? 'opacity-45' : ''
                }`}
                style={{
                  top: y(b.start_min),
                  height: h,
                  left: `calc(52px + ${b.lane * laneW}% - ${b.lane * laneW * 0.52}px)`,
                  width: `calc(${laneW}% - ${laneW * 0.52}px - 4px)`,
                  background: isAnchor ? 'transparent' : `${c}21`,
                  border: isAnchor ? `1.5px dashed ${c}` : undefined,
                  borderLeft: isAnchor ? undefined : `3px solid ${c}`,
                }}
              >
                <div className={`truncate font-medium text-text-primary ${
                  missed ? 'line-through' : ''
                }`}>
                  {done ? '✓ ' : ''}{b.pinned ? '📌 ' : ''}
                  {b.title || KIND_LABEL[b.kind] || b.kind}
                </div>
                {h >= 34 && (
                  <div className="text-[10px] text-text-muted">
                    {fmtMin(b.start_min)}–{fmtMin(b.end_min)}
                  </div>
                )}
              </div>
            )
          })}
          {/* now line */}
          {showNow && (
            <div
              className="absolute left-11 right-0 z-10 pointer-events-none"
              style={{ top: y(nowMin) }}
            >
              <div className="border-t-2 border-red-400/80" />
              <div className="absolute -left-1 -top-[5px] w-2 h-2 rounded-full bg-red-400" />
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// ── week view: 7 mini timelines ─────────────────────────────────────

function WeekView({ start, today, byDay, heatFor, onPickDay }: {
  start: string
  today: string
  byDay: Map<string, Block[]>
  heatFor: (d: string) => DayHeat | undefined
  onPickDay: (d: string) => void
}) {
  const days = Array.from({ length: 7 }, (_, i) => localDay(i, start))
  const START = 6 * 60
  const END = 23 * 60
  const H = 420
  const y = (m: number) =>
    (Math.min(Math.max(m, START), END) - START) / (END - START) * H

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <div className="grid grid-cols-7 gap-2">
        {days.map((d) => {
          const blocks = byDay.get(d) || []
          const water = blocks.filter((b) => b.kind === 'downtime')
          const boats = assignLanes(blocks.filter((b) => b.kind !== 'downtime'))
          const label = new Date(d + 'T12:00:00').toLocaleDateString(
            undefined, { weekday: 'short' })
          const wk = isWeekend(d)
          const z = heatFor(d)?.z
          return (
            <div
              key={d}
              className={`min-w-0 ${d === days[5] ? 'border-l border-border/40 pl-1.5' : ''}`}
            >
              <button
                onClick={() => onPickDay(d)}
                title={`Open ${d}`}
                className={`w-full text-center text-xs pb-1 cursor-pointer rounded ${
                  d === today
                    ? 'text-accent font-semibold'
                    : wk
                      ? 'text-text-secondary hover:text-text-primary'
                      : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {label} <span className="opacity-70">{d.slice(8)}</span>
              </button>
              {z != null && (
                <div className="mb-1" title={`slept ${z}h`}>
                  <div className="h-1 rounded bg-surface-tertiary/60 overflow-hidden">
                    <div
                      className="h-full rounded"
                      style={{
                        width: `${sleepFrac(z) * 100}%`,
                        background: 'rgba(53,169,155,0.75)',
                      }}
                    />
                  </div>
                  <div className="text-right text-[9px] text-text-muted leading-tight">
                    {z}h
                  </div>
                </div>
              )}
              <div
                className={`relative rounded overflow-hidden ${
                  wk ? 'bg-surface-tertiary/50' : 'bg-surface-tertiary/30'
                }`}
                style={{ height: H }}
              >
                {water.map((b) => (
                  <div
                    key={b.id}
                    className="absolute left-0 right-0"
                    style={{
                      top: y(b.start_min),
                      height: Math.max(4, y(b.end_min) - y(b.start_min)),
                      background: 'rgba(61,74,92,0.18)',
                    }}
                  />
                ))}
                {boats.map((b) => {
                  const c = kindColor(b.kind)
                  const isAnchor = b.kind === 'anchor'
                  const missed = b.state === 'missed' || b.state === 'skipped'
                  const bh = Math.max(10, y(b.end_min) - y(b.start_min) - 1)
                  const laneW = 100 / b.lanes
                  return (
                    <div
                      key={b.id}
                      title={`${b.title || b.kind} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)} · ${b.state}`}
                      className={`absolute rounded-sm px-1 overflow-hidden ${
                        missed ? 'opacity-40' : ''
                      }`}
                      style={{
                        top: y(b.start_min),
                        height: bh,
                        left: `${b.lane * laneW}%`,
                        width: `calc(${laneW}% - 2px)`,
                        background: isAnchor ? 'transparent' : `${c}30`,
                        border: isAnchor ? `1px dashed ${c}` : undefined,
                        borderLeft: isAnchor ? undefined : `2px solid ${c}`,
                      }}
                    >
                      {bh >= 16 && (
                        <span className={`text-[9px] text-text-primary truncate block ${
                          missed ? 'line-through' : ''
                        }`}>
                          {b.state === 'done' ? '✓' : ''}{b.title || b.kind}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── month view: planned-hours heat calendar ─────────────────────────

function MonthView({ anchorDay, today, byDay, heatFor, onPickDay }: {
  anchorDay: string
  today: string
  byDay: Map<string, Block[]>
  heatFor: (d: string) => DayHeat | undefined
  onPickDay: (d: string) => void
}) {
  const first = anchorDay.slice(0, 8) + '01'
  const monthLabel = new Date(first + 'T12:00:00').toLocaleDateString(
    undefined, { month: 'long', year: 'numeric' })
  const gridStart = weekStart(first)
  const cells = Array.from({ length: 42 }, (_, i) => localDay(i, gridStart))
  const month = anchorDay.slice(0, 7)

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">{monthLabel}</h3>
      <div className="relative">
        <div className="grid grid-cols-7 gap-1.5 text-center text-[10px] mb-1">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((w, i) => (
            <div key={w} className={i >= 5 ? 'text-text-secondary' : 'text-text-muted'}>{w}</div>
          ))}
        </div>
        {/* Weekend seam: Fri|Sat boundary as a quiet vertical rule. */}
        <div
          className="absolute top-0 bottom-0 w-px bg-border/60 pointer-events-none"
          style={{ left: 'calc(5 / 7 * 100%)' }}
        />
        <div className="grid grid-cols-7 gap-1.5">
          {cells.map((d) => {
            const inMonth = d.slice(0, 7) === month
            const blocks = (byDay.get(d) || []).filter(
              (b) => b.kind !== 'downtime')
            const planned = blocks.reduce(
              (s, b) => s + (b.end_min - b.start_min), 0)
            const done = blocks.filter((b) => b.state === 'done').length
            const h = heatFor(d)
            // Background = what HAPPENED (whole corpus); the plan is
            // shape + text (left edge, hour line), never a second
            // alpha wash on the same hue.
            const lived = (h?.s || 0) + (h?.t || 0)
            const wk = isWeekend(d)
            const plannerLine = blocks.length
              ? `${(planned / 60).toFixed(1)}h planned, ${done}/${blocks.length} done`
              : ''
            const heatLine = dayHeatTitle(d, h)
            const titleText = plannerLine
              ? `${d}: ${plannerLine}${heatLine !== d ? ' · ' + heatLine.slice(d.length + 2) : ''}`
              : heatLine
            return (
              <button
                key={d}
                onClick={() => onPickDay(d)}
                title={titleText}
                className={`relative h-16 rounded-md border text-left p-1.5 cursor-pointer transition-colors overflow-hidden ${
                  d === today
                    ? 'border-accent'
                    : 'border-border/50 hover:border-border'
                } ${inMonth ? '' : 'opacity-35'}`}
                style={{
                  background: lived > 0
                    ? monthShade(lived)
                    : wk && inMonth ? 'rgba(255,255,255,0.03)' : 'transparent',
                  borderLeft: inMonth && blocks.length
                    ? '2px solid #7c5cff' : undefined,
                }}
              >
                <span className={`text-[11px] ${
                  d === today ? 'text-accent font-semibold' : 'text-text-secondary'
                }`}>
                  {Number(d.slice(8))}
                </span>
                {blocks.length > 0 && (
                  <span className="absolute bottom-[7px] left-1.5 right-1.5 text-[9px] text-text-muted truncate">
                    {(planned / 60).toFixed(1)}h{done ? ` · ${done}✓` : ''}
                  </span>
                )}
                {h?.z != null && (
                  <span
                    className="absolute bottom-0 left-0 h-[3px] rounded-b"
                    style={{
                      width: `${sleepFrac(h.z) * 100}%`,
                      background: 'rgba(53,169,155,0.6)',
                    }}
                  />
                )}
              </button>
            )
          })}
        </div>
      </div>
      <div className="text-[10px] text-text-muted mt-2">
        Shading = what happened that day (AI + logged hours, from the whole
        corpus). Purple left edge and the hour text = the phone's plan. Teal
        strip along the bottom = sleep on the night ending that morning
        (full width = 10h); no strip = no tracker data. Click a day to open it.
      </div>
    </section>
  )
}

// ── year view: the year as texture, from the whole corpus ──────────
// 12 month rows, each day a sliver whose brightness is that day's
// activity (AI-session minutes + logged time). Works for ANY year
// the corpus covers, which is the point: permanent memory. Hover a
// day for the full mix (sessions, logged, sleep, steps); click to
// open it.

function dayHeatTitle(day: string, h: DayHeat | undefined): string {
  if (!h) return day
  const parts: string[] = []
  if (h.sc) parts.push(`${h.sc} AI session${h.sc === 1 ? '' : 's'} ${((h.s || 0) / 60).toFixed(1)}h`)
  if (h.t) parts.push(`${(h.t / 60).toFixed(1)}h logged`)
  if (h.z) parts.push(`slept ${h.z}h`)
  if (h.p) parts.push(`${h.p.toLocaleString()} steps`)
  if (h.a) parts.push(`${h.a} active-zone min`)
  return parts.length ? `${day}: ${parts.join(' · ')}` : day
}

function YearView({ year, today, heat, loading, error, onRetry, onPickDay }: {
  year: number
  today: string
  heat: Record<string, DayHeat>
  loading: boolean
  error: boolean
  onRetry: () => void
  onPickDay: (d: string) => void
}) {
  const shade = activityShade
  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">
        {year}
        {loading && (
          <span className="ml-2 text-xs font-normal text-text-muted animate-pulse">
            reading the corpus…
          </span>
        )}
        {error && (
          <span className="ml-2 text-xs font-normal text-red-400">
            corpus unreachable (is the Pi up?){' '}
            <button
              onClick={onRetry}
              className="underline cursor-pointer hover:text-red-300"
            >
              retry
            </button>
          </span>
        )}
      </h3>
      {(() => {
        // Day-of-week aligned week columns, Monday start. Sat/Sun sit
        // below a physical gap so the weekly rhythm is a shape, not a
        // color. Each day = 12x12 activity cell + 3px sleep lane.
        const CELL = 12, COLW = 14, GUT = 26, TOPBAND = 14
        const ROWPITCH = 19, WEEKEND_GAP = 6
        const gridStart = weekStart(`${year}-01-01`)
        const cols: string[] = []
        for (let m = gridStart; m <= `${year}-12-31`; m = localDay(7, m)) cols.push(m)
        const width = GUT + cols.length * COLW
        const height = TOPBAND + 7 * ROWPITCH + WEEKEND_GAP
        const rowY = (dow: number) =>
          TOPBAND + dow * ROWPITCH + (dow >= 5 ? WEEKEND_GAP : 0)
        const monthLabels: { x: number; text: string }[] = []
        let seen = ''
        cols.forEach((mon, ci) => {
          const mkey = mon.slice(0, 7)
          if (mkey !== seen && mon.slice(0, 4) === String(year)) {
            seen = mkey
            monthLabels.push({
              x: GUT + ci * COLW,
              text: new Date(mon + 'T12:00:00').toLocaleDateString(
                undefined, { month: 'short' }),
            })
          }
        })
        return (
          <div className="overflow-x-auto">
            <svg
              viewBox={`0 0 ${width} ${height}`}
              style={{ width: '100%', minWidth: 700, height: 'auto' }}
              role="img"
              aria-label={`Activity and sleep for every day of ${year}`}
            >
              {monthLabels.map((m) => (
                <text key={m.text} x={m.x} y={10} fontSize="9" fill={TXT_MUTED}>
                  {m.text}
                </text>
              ))}
              {['Mon', '', 'Wed', '', 'Fri', 'Sat', 'Sun'].map((lbl, dow) =>
                lbl ? (
                  <text
                    key={lbl} x={0} y={rowY(dow) + CELL / 2 + 3} fontSize="9"
                    fill={dow >= 5 ? TXT_SECONDARY : TXT_MUTED}
                  >
                    {lbl}
                  </text>
                ) : null)}
              <rect
                x={GUT} y={rowY(5) - 2} width={cols.length * COLW}
                height={2 * ROWPITCH + 1} fill="rgba(255,255,255,0.03)" rx={3}
              />
              {cols.map((mon, ci) =>
                Array.from({ length: 7 }, (_, dow) => {
                  const day = localDay(dow, mon)
                  if (day.slice(0, 4) !== String(year)) return null
                  const h = heat[day]
                  const min = (h?.s || 0) + (h?.t || 0)
                  const hasAny = !!h && (min > 0 || !!h.sc || !!h.z || !!h.p)
                  const x = GUT + ci * COLW
                  const y = rowY(dow)
                  return (
                    <g
                      key={day} onClick={() => onPickDay(day)}
                      style={{ cursor: 'pointer' }}
                    >
                      <title>{dayHeatTitle(day, h)}</title>
                      <rect
                        x={x} y={y} width={CELL} height={CELL} rx={2}
                        fill={min > 0 ? shade(min)
                          : hasAny ? 'rgba(255,255,255,0.09)'
                          : 'rgba(255,255,255,0.04)'}
                      />
                      {h?.z != null && (
                        <rect
                          x={x} y={y + 13} width={CELL * sleepFrac(h.z)}
                          height={3} rx={1} fill="rgba(53,169,155,0.7)"
                        />
                      )}
                      {day === today && (
                        <rect
                          x={x - 1} y={y - 1} width={CELL + 2} height={CELL + 5}
                          rx={2} fill="none" stroke="#7c5cff" strokeWidth={1.5}
                        />
                      )}
                    </g>
                  )
                }))}
            </svg>
          </div>
        )
      })()}
      <div className="text-[10px] text-text-muted mt-2">
        Columns are weeks; Sat/Sun sit below the gap. Purple = AI-session +
        logged hours that day (parallel sessions sum, so heavy days can top
        24h). Teal underline = hours slept the night ending that morning; a
        full-width line is 10h. Teal gaps = no tracker data, not no sleep.
        Hover for the mix; click a day to open everything Cortex holds for it.
      </div>
    </section>
  )
}

// ── rhythm card: the week as a shape (median + middle half) ────────
// Per-weekday small multiples for activity, sleep, and steps, plus a
// coverage barcode showing how much of the period the corpus holds.
// Descriptive only: no targets, no deltas, the eye does the comparing.

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function RhythmRow({ label, hue, byDow, fmt }: {
  label: string
  hue: string
  byDow: number[][]
  fmt: (v: number) => string
}) {
  const stats = byDow.map((vals) => {
    const sorted = [...vals].sort((a, b) => a - b)
    return {
      n: sorted.length,
      q1: quantile(sorted, 0.25),
      med: quantile(sorted, 0.5),
      q3: quantile(sorted, 0.75),
    }
  })
  const scaleMax = Math.max(...stats.map((s) => s.q3)) * 1.1 || 1
  const yPct = (v: number) => 100 - Math.min(100, (v / scaleMax) * 100)
  return (
    <div className="flex items-end gap-2">
      <div className="w-20 shrink-0 flex items-center gap-1.5 text-[11px] text-text-secondary pb-1">
        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: hue }} />
        {label}
      </div>
      <div className="flex-1 flex">
        {stats.map((s, dow) => (
          <div
            key={dow}
            className={`flex-1 flex flex-col items-center ${
              dow === 5 ? 'border-l border-border/40' : ''
            } ${dow >= 5 ? 'bg-white/[0.03] rounded' : ''}`}
            title={s.n
              ? `${DOW_LABELS[dow]}: median ${fmt(s.med)}, middle half ${fmt(s.q1)}-${fmt(s.q3)}, ${s.n} days`
              : `${DOW_LABELS[dow]}: no data`}
          >
            <div className="relative h-11 w-full">
              {s.n > 0 && (
                <>
                  <div
                    className="absolute left-1/2 -translate-x-1/2 w-[5px] rounded-full"
                    style={{
                      top: `${yPct(s.q3)}%`,
                      height: `${Math.max(4, yPct(s.q1) - yPct(s.q3))}%`,
                      background: hue, opacity: 0.28,
                    }}
                  />
                  <div
                    className="absolute left-1/2 -translate-x-1/2 w-[5px] h-[5px] rounded-full"
                    style={{ top: `calc(${yPct(s.med)}% - 2.5px)`, background: hue }}
                  />
                </>
              )}
            </div>
            <div className={`text-[9px] ${dow >= 5 ? 'text-text-secondary' : 'text-text-muted'}`}>
              {DOW_LABELS[dow]}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function RhythmCard({ dates, label, today, heatFor }: {
  dates: string[]
  label: string
  today: string
  heatFor: (d: string) => DayHeat | undefined
}) {
  const scope = useMemo(
    () => deriveScope(heatFor, dates, today), [heatFor, dates, today])
  const dataDays = dates.filter((d) => heatFor(d)).length
  if (dataDays < 14) return null

  const rows: { label: string; hue: string; byDow: number[][]; fmt: (v: number) => string }[] = []
  const activeDays = scope.activeByDow.reduce((s, a) => s + a.length, 0)
  if (activeDays >= 8) {
    rows.push({
      label: 'AI + logged', hue: '#7c5cff', byDow: scope.activeByDow,
      fmt: (v) => `${(v / 60).toFixed(1)}h`,
    })
  }
  const sleepDays = scope.sleepByDow.reduce((s, a) => s + a.length, 0)
  if (sleepDays >= 8) {
    rows.push({
      label: 'Sleep', hue: SLEEP_TEAL, byDow: scope.sleepByDow,
      fmt: (v) => `${v.toFixed(1)}h`,
    })
  }
  const stepDays = scope.stepsByDow.reduce((s, a) => s + a.length, 0)
  if (stepDays >= 8) {
    rows.push({
      label: 'Steps', hue: '#8b949e', byDow: scope.stepsByDow,
      fmt: (v) => `${(v / 1000).toFixed(1)}k`,
    })
  }

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">
        Weekly rhythm · {label}
      </h3>
      <div className="space-y-2">
        {rows.map((r) => (
          <RhythmRow key={r.label} {...r} />
        ))}
      </div>
      <div className="mt-4">
        <div className="text-xs font-medium text-text-secondary mb-1.5">In the corpus</div>
        <div className="space-y-1">
          <CoverageBar label="sessions" hue="rgba(124,92,255,0.7)" dates={dates}
            heatFor={heatFor} has={(h) => !!h?.sc} count={`${scope.counts.sessions} d`} />
          <CoverageBar label="logged" hue="rgba(139,148,158,0.7)" dates={dates}
            heatFor={heatFor} has={(h) => !!h?.t} count={`${scope.counts.logged} d`} />
          <CoverageBar label="sleep" hue="rgba(53,169,155,0.7)" dates={dates}
            heatFor={heatFor} has={(h) => h?.z != null} count={`${scope.counts.nights} n`} />
        </div>
      </div>
      <div className="text-[10px] text-text-muted mt-3">
        Median dot with the middle-half band, per weekday. Quiet days count
        as zero for activity; untracked nights are absent, not zero. Sleep
        from {scope.counts.nights} tracked nights.
      </div>
    </section>
  )
}

function CoverageBar({ label, hue, dates, heatFor, has, count }: {
  label: string
  hue: string
  dates: string[]
  heatFor: (d: string) => DayHeat | undefined
  has: (h: DayHeat | undefined) => boolean
  count: string
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 shrink-0 text-[9px] text-text-muted">{label}</div>
      <svg
        viewBox={`0 0 ${dates.length} 6`}
        preserveAspectRatio="none"
        className="flex-1 h-1.5"
      >
        {dates.map((d, i) => (has(heatFor(d)) ? (
          <rect key={i} x={i} width={1} height={6} fill={hue} />
        ) : null))}
      </svg>
      <div className="w-12 shrink-0 text-right text-[10px] text-text-muted font-mono">{count}</div>
    </div>
  )
}

// ── day corpus panel: everything Cortex holds about one day ────────
// The permanent-memory half of the Day view: AI sessions (with gist
// one-liners), logged time, health (sleep, steps, scores), journal
// entries, and the daily narrative. Works for any date, any year,
// independent of the phone's mirrored planner window.

const HEALTH_CHIPS: [string, (v: number) => string][] = [
  ['steps', (v) => `${Math.round(v).toLocaleString()} steps`],
  ['sleep_minutes', (v) => `slept ${(v / 60).toFixed(1)}h`],
  ['sleep_score', (v) => `sleep score ${Math.round(v)}`],
  ['resting_hr', (v) => `resting HR ${Math.round(v)}`],
  ['stress_score', (v) => `stress ${Math.round(v)}`],
  ['azm_minutes', (v) => `${Math.round(v)} active-zone min`],
]

function fmtClock(iso?: string): string {
  const t = (iso || '').slice(11, 16)
  return t || ''
}

// The day as it actually happened: a 24h strip of AI sessions
// (filled) and logged time (outlined), adjacent to the plan above but
// never diffed against it. Marks with unknown start times stack at
// the right edge so counts stay honest.
function pctOfDay(iso?: string): number | null {
  const t = (iso || '').slice(11, 16)
  if (!/^\d{2}:\d{2}$/.test(t)) return null
  const [hh, mm] = t.split(':').map(Number)
  return ((hh * 60 + mm) / 1440) * 100
}

function DayRibbon({ d }: { d: DayDetail }) {
  const rows: {
    label: string
    marks: { pct: number | null; widthPct: number; title: string }[]
    filled: boolean
  }[] = []
  if (d.sessions.length) {
    rows.push({
      label: 'AI',
      filled: true,
      marks: d.sessions.map((s) => {
        const pct = pctOfDay(s.local_started_at)
        const startMin = pct == null ? 0 : (pct / 100) * 1440
        const widthPct = Math.max(
          0.25,
          (Math.min(s.duration_minutes || 0, 1440 - startMin) / 1440) * 100)
        return {
          pct, widthPct,
          title: `${pct == null ? 'time unknown · ' : fmtClock(s.local_started_at) + ' · '}${s.project || s.source} · ${s.duration_minutes}m · ${s.message_count} msgs${s.gist ? ' · ' + s.gist.split('\n')[0] : ''}`,
        }
      }),
    })
  }
  if (d.time_entries.length) {
    rows.push({
      label: 'logged',
      filled: false,
      marks: d.time_entries.map((t) => {
        const pct = pctOfDay(t.local_started_at)
        const startMin = pct == null ? 0 : (pct / 100) * 1440
        const widthPct = Math.max(
          0.25,
          (Math.min(t.duration_minutes || 0, 1440 - startMin) / 1440) * 100)
        return {
          pct, widthPct,
          title: `${pct == null ? 'time unknown · ' : fmtClock(t.local_started_at) + ' · '}${t.project_tag} · ${t.duration_minutes}m${t.description ? ' · ' + t.description : ''}`,
        }
      }),
    })
  }
  if (!rows.length) return null
  return (
    <div className="mb-1">
      <div className="relative h-12 rounded bg-surface-tertiary/30">
        {[0, 25, 50, 75].map((pct, i) => (
          <div
            key={pct}
            className="absolute top-0 bottom-0 border-l border-border/30"
            style={{ left: `${pct}%` }}
          >
            <span className="absolute top-0 left-0.5 text-[9px] text-text-muted">
              {['12am', '6am', '12pm', '6pm'][i]}
            </span>
          </div>
        ))}
        {rows.map((row, ri) => {
          const top = rows.length === 1 ? '45%' : ri === 0 ? '28%' : '66%'
          let unknownIdx = 0
          return (
            <div key={row.label}>
              <span
                className="absolute left-0.5 text-[9px] text-text-muted"
                style={{ top: `calc(${top} - 1px)` }}
              >
                {row.label}
              </span>
              {row.marks.map((m, i) => (
                <div
                  key={i}
                  title={m.title}
                  className="absolute h-2.5 rounded-sm"
                  style={{
                    top,
                    ...(m.pct == null
                      ? { right: 2 + (unknownIdx++) * 6, width: 4 }
                      : { left: `${m.pct}%`, width: `${m.widthPct}%` }),
                    ...(row.filled
                      ? { background: 'rgba(124,92,255,0.55)' }
                      : { border: '1px solid rgba(124,92,255,0.7)' }),
                  }}
                />
              ))}
            </div>
          )
        })}
      </div>
      <div className="text-[10px] text-text-muted mt-1">
        The day as it happened: filled = AI sessions, outlined = logged time.
        The plan lives in the timeline above.
      </div>
    </div>
  )
}

function DayCorpusCard({ date }: { date: string }) {
  const [detail, setDetail] = useState<DayDetail | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  useEffect(() => {
    let stale = false
    setState('loading')
    apiFetch<DayDetail>(`/overseer/day?date=${date}`)
      .then((r) => {
        if (stale) return
        // The proxy wraps Pi-down as HTTP 200 + ok:false; that is an
        // ERROR, never "this day is empty" (the Pi returns ok:true
        // for genuinely empty days).
        if (r && r.ok) { setDetail(r); setState('ready') }
        else { setDetail(null); setState('error') }
      })
      .catch(() => { if (!stale) { setDetail(null); setState('error') } })
    return () => { stale = true }
  }, [date])

  if (state === 'loading') {
    return (
      <section className="rounded-lg border border-border bg-surface-secondary p-4">
        <div className="text-xs text-text-muted animate-pulse">Reading the corpus…</div>
      </section>
    )
  }
  const d = detail
  const empty = !d || (
    d.sessions.length === 0 && d.time_entries.length === 0 &&
    Object.keys(d.health).length === 0 && d.journal.length === 0 &&
    !d.narrative)

  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4 space-y-3">
      <h3 className="text-sm font-semibold text-text-primary">
        This day in Cortex
      </h3>
      {d && (d.sessions.length > 0 || d.time_entries.length > 0) && (
        <DayRibbon d={d} />
      )}
      {empty && (
        <div className="text-xs text-text-muted">
          {state === 'error'
            ? 'Corpus unreachable (is the Pi up?).'
            : 'No corpus data for this day.'}
        </div>
      )}

      {d && Object.keys(d.health).length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {HEALTH_CHIPS.map(([k, fmt]) =>
            d.health[k] != null ? (
              <span
                key={k}
                className="px-2 py-0.5 rounded-full border border-border text-[11px] text-text-secondary"
              >
                {fmt(d.health[k])}
              </span>
            ) : null)}
        </div>
      )}

      {d && d.sessions.length > 0 && (
        <div>
          <div className="text-xs font-medium text-text-secondary mb-1.5">
            AI sessions · {d.sessions.length} ·{' '}
            {(d.session_minutes / 60).toFixed(1)}h
          </div>
          <div className="space-y-1.5">
            {d.sessions.map((s) => (
              <div key={s.id} className="text-xs border-l-2 border-accent/40 pl-2">
                <span className="text-text-muted">{fmtClock(s.local_started_at)}</span>{' '}
                <span className="text-text-primary font-medium">{s.project || s.source}</span>{' '}
                <span className="text-text-muted">
                  · {s.duration_minutes}m · {s.message_count} msgs
                  {s.redacted ? ' · redacted' : ''}
                </span>
                {s.gist && (
                  <div className="text-text-secondary mt-0.5">{s.gist}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {d && d.time_entries.length > 0 && (
        <div>
          <div className="text-xs font-medium text-text-secondary mb-1.5">
            Logged time · {(d.logged_minutes / 60).toFixed(1)}h
          </div>
          <div className="space-y-1">
            {d.time_entries.map((t, i) => (
              <div key={i} className="text-xs text-text-secondary">
                <span className="text-text-muted">{fmtClock(t.local_started_at)}</span>{' '}
                <span className="font-medium">{t.project_tag}</span>{' '}
                · {t.duration_minutes}m
                {t.description ? ` · ${t.description}` : ''}
              </div>
            ))}
          </div>
        </div>
      )}

      {d && d.journal.length > 0 && (
        <div>
          <div className="text-xs font-medium text-text-secondary mb-1.5">Journal</div>
          <div className="space-y-1.5">
            {d.journal.map((j, i) => (
              <div key={i} className="text-xs text-text-secondary whitespace-pre-wrap">
                {j.text}
              </div>
            ))}
          </div>
        </div>
      )}

      {d && d.narrative && (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-secondary font-medium">
            Daily narrative
          </summary>
          <div className="mt-1.5 text-text-secondary whitespace-pre-wrap">
            {d.narrative}
          </div>
        </details>
      )}
    </section>
  )
}
