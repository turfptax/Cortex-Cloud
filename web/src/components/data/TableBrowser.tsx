import { useState, useMemo } from 'react'
import { fmtTime, fmtLocalWithTz, fmtUtcWithTz } from '../../lib/time'

interface Props {
  rows: Record<string, any>[]
  loading: boolean
  onRowClick: (row: Record<string, any>) => void
  activeTable: string
}

// Columns to prioritize per table
const COLUMN_ORDER: Record<string, string[]> = {
  projects: ['tag', 'name', 'total_hours', 'category', 'org_tag', 'status', 'github_url'],
  organizations: ['tag', 'name', 'org_type', 'my_role', 'is_active'],
  time_entries: ['started_at', 'project_tag', 'org_tag', 'activity_type', 'description', 'duration_minutes'],
  notes: ['created_at', 'content', 'note_type', 'tags', 'project'],
  people: ['id', 'name', 'role', 'email', 'projects'],
  sessions: ['id', 'ai_platform', 'started_at', 'ended_at', 'summary'],
}

// Columns to hide (internal/noisy)
// local_*_at columns are hidden because their content is rendered
// via the paired UTC column (formatCell looks up the local sibling
// and prefers it). Showing both would be redundant duplicate columns.
const HIDDEN_COLS = new Set(['session_id', 'source', 'last_touched'])
function isHidden(key: string): boolean {
  if (HIDDEN_COLS.has(key)) return true
  if (key.startsWith('local_') && key.endsWith('_at')) return true
  return false
}

// Columns that should be wider
const WIDE_COLS = new Set(['description', 'content', 'summary', 'notes', 'name', 'github_url'])

// Columns with mono font
const MONO_COLS = new Set(['tag', 'project_tag', 'org_tag', 'id', 'duration_minutes', 'total_hours'])

function formatCell(key: string, value: any, row?: Record<string, any>): string {
  if (value === null || value === undefined || value === '') return '—'
  if (key === 'total_hours' || key === 'duration_minutes') {
    const n = Number(value)
    if (key === 'duration_minutes') return `${n}m`
    return `${n.toFixed(1)}h`
  }
  // Slice 9.4.1: timestamp display rule (memory/feedback_time_always_local_with_tz.md):
  // every shown time MUST include its timezone. After the 9.4.1 migration,
  // every `_at` column has a paired `local_<col>_at` column with explicit
  // offset. Prefer the local variant; fall back to UTC-with-suffix for any
  // legacy row that somehow missed the trigger.
  if (key.startsWith('local_') && key.endsWith('_at')) {
    return fmtLocalWithTz(String(value))
  }
  if (key.endsWith('_at')) {
    const localKey = 'local_' + key
    if (row && row[localKey]) {
      return fmtTime(String(row[localKey]), String(value))
    }
    return fmtUtcWithTz(String(value))
  }
  if (key === 'is_active') return value ? '✓ Active' : '✗ Inactive'
  const s = String(value)
  return s.length > 80 ? s.slice(0, 77) + '…' : s
}

function colLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
    .replace('Url', 'URL')
    .replace('Id', 'ID')
}

export function TableBrowser({ rows, loading, onRowClick, activeTable }: Props) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortAsc, setSortAsc] = useState(true)
  const [filter, setFilter] = useState('')

  // Determine columns from first row + preferred order
  const columns = useMemo(() => {
    if (rows.length === 0) return []
    const allKeys = Object.keys(rows[0]).filter(k => !isHidden(k))
    const preferred = COLUMN_ORDER[activeTable] || []
    const ordered = preferred.filter(k => allKeys.includes(k))
    const rest = allKeys.filter(k => !ordered.includes(k))
    return [...ordered, ...rest]
  }, [rows, activeTable])

  // Filter rows
  const filtered = useMemo(() => {
    if (!filter.trim()) return rows
    const q = filter.toLowerCase()
    return rows.filter(row =>
      Object.values(row).some(v =>
        v !== null && String(v).toLowerCase().includes(q)
      )
    )
  }, [rows, filter])

  // Sort rows
  const sorted = useMemo(() => {
    if (!sortKey) return filtered
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? ''
      const bv = b[sortKey] ?? ''
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av
      }
      const sa = String(av).toLowerCase()
      const sb = String(bv).toLowerCase()
      return sortAsc ? sa.localeCompare(sb) : sb.localeCompare(sa)
    })
  }, [filtered, sortKey, sortAsc])

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Search bar */}
      <div className="px-4 py-2.5 border-b border-border bg-surface">
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-xs">🔍</span>
          <input
            type="text"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder={`Search ${activeTable.replace(/_/g, ' ')}...`}
            className="w-full bg-surface-secondary text-text-primary text-sm rounded-lg pl-8 pr-3 py-2 border border-border focus:border-accent focus:outline-none placeholder:text-text-muted/60 transition-colors"
          />
          {filter && (
            <button
              onClick={() => setFilter('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary text-xs cursor-pointer"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2">
            <div className="w-6 h-6 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
            <span className="text-text-muted text-xs">Loading data...</span>
          </div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2">
            <span className="text-2xl opacity-40">{filter ? '🔍' : '📭'}</span>
            <span className="text-text-muted text-sm">
              {filter ? 'No matching rows' : 'No data in this table'}
            </span>
            {filter && (
              <button
                onClick={() => setFilter('')}
                className="text-accent text-xs hover:underline cursor-pointer"
              >
                Clear filter
              </button>
            )}
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-surface-tertiary/80 sticky top-0 z-10 backdrop-blur-sm">
              <tr>
                {columns.map(col => (
                  <th
                    key={col}
                    onClick={() => toggleSort(col)}
                    className={`px-3 py-2.5 text-left text-[11px] font-semibold tracking-wide uppercase cursor-pointer select-none whitespace-nowrap transition-colors ${
                      sortKey === col
                        ? 'text-accent'
                        : 'text-text-muted hover:text-text-primary'
                    }`}
                  >
                    <span className="inline-flex items-center gap-1">
                      {colLabel(col)}
                      {sortKey === col ? (
                        <span className="text-[10px]">{sortAsc ? '▲' : '▼'}</span>
                      ) : (
                        <span className="text-[10px] opacity-0 group-hover:opacity-30">▼</span>
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr
                  key={row.id || row.tag || i}
                  onClick={() => onRowClick(row)}
                  className={`cursor-pointer transition-colors border-b border-border/50 hover:bg-accent/5 ${
                    i % 2 === 0 ? 'bg-surface' : 'bg-surface-secondary/30'
                  }`}
                >
                  {columns.map(col => (
                    <td
                      key={col}
                      className={`px-3 py-2.5 text-text-secondary whitespace-nowrap truncate ${
                        WIDE_COLS.has(col) ? 'max-w-[350px]' : 'max-w-[200px]'
                      } ${MONO_COLS.has(col) ? 'font-mono text-text-muted' : ''}`}
                      title={String(row[col] ?? '')}
                    >
                      {col === 'status' ? (
                        <StatusBadge value={row[col]} />
                      ) : col === 'note_type' ? (
                        <TypeBadge value={row[col]} />
                      ) : col === 'activity_type' ? (
                        <TypeBadge value={row[col]} />
                      ) : col === 'is_active' ? (
                        <span className={row[col] ? 'text-success' : 'text-text-muted'}>
                          {row[col] ? '✓ Active' : '✗ Inactive'}
                        </span>
                      ) : (
                        formatCell(col, row[col], row)
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-border bg-surface-secondary/50 flex items-center justify-between">
        <span className="text-xs text-text-muted">
          {sorted.length.toLocaleString()} row{sorted.length !== 1 ? 's' : ''}
          {filter && (
            <span className="text-text-muted/60">
              {' '}(filtered from {rows.length.toLocaleString()})
            </span>
          )}
        </span>
        <span className="text-[10px] text-text-muted/50">
          Click a row to edit
        </span>
      </div>
    </div>
  )
}

// --- Sub-components ---

function StatusBadge({ value }: { value: any }) {
  const s = String(value || '').toLowerCase()
  const colors: Record<string, string> = {
    active: 'bg-success/15 text-success',
    completed: 'bg-accent/15 text-accent',
    paused: 'bg-warning/15 text-warning',
    archived: 'bg-text-muted/15 text-text-muted',
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${colors[s] || 'bg-surface-tertiary text-text-muted'}`}>
      {s || '—'}
    </span>
  )
}

function TypeBadge({ value }: { value: any }) {
  const s = String(value || '')
  if (!s) return <span className="text-text-muted">—</span>
  return (
    <span className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-surface-tertiary text-text-secondary">
      {s}
    </span>
  )
}
