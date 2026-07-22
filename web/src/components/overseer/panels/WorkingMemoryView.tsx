import { useEffect, useState } from 'react'
import { apiFetch } from '../../../lib/api'
import { fmtTime } from '../../../lib/time'
import {
  type DetailResp,
  type WorkingMemory,
} from '../shared'

export function StatCard({
  label,
  value,
  sub,
}: {
  label: string
  value: number | string
  sub?: string
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-secondary p-3">
      <div className="text-xs text-text-muted uppercase tracking-wide">{label}</div>
      <div className="text-2xl font-semibold text-text-primary mt-1">{value}</div>
      {sub && <div className="text-xs text-text-muted mt-1">{sub}</div>}
    </div>
  )
}

export function Row({ label, value }: { label: string; value: any }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-text-muted">{label}</dt>
      <dd className="text-text-primary text-right truncate max-w-xs">{String(value)}</dd>
    </div>
  )
}

// ── Slice 3g #2: drill-down chip + inline detail card ─────────

export function TokenChip({
  token,
  active,
  onClick,
  className,
}: {
  token?: string | null
  active?: boolean
  onClick: (token: string) => void
  className?: string
}) {
  if (!token) return null
  return (
    <button
      onClick={() => onClick(token)}
      className={`text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded cursor-pointer transition-colors font-mono ${
        active
          ? 'bg-accent text-white'
          : 'bg-surface-tertiary text-text-muted hover:bg-accent/30 hover:text-accent-hover'
      } ${className || ''}`}
      title={`Drill into ${token}`}
    >
      {token}
    </button>
  )
}

export function DetailCard({
  token,
  onNavigate,
  onClose,
}: {
  token: string
  onNavigate: (token: string) => void
  onClose: () => void
}) {
  const [resp, setResp] = useState<DetailResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    setResp(null)
    apiFetch<DetailResp>(`/overseer/detail?token=${encodeURIComponent(token)}`)
      .then((r) => {
        if (cancelled) return
        if (r.ok) setResp(r)
        else setError(r.error || 'detail failed')
      })
      .catch((e) => {
        if (!cancelled) setError(String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  return (
    <div className="bg-surface-secondary border border-accent/40 rounded-lg p-4 mb-4 text-xs">
      <div className="flex items-baseline justify-between mb-2">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] uppercase tracking-wide text-accent font-mono">
            {token}
          </span>
          {resp?.type && (
            <span className="text-text-muted text-[10px] uppercase">
              · {resp.type}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary text-[10px] uppercase cursor-pointer"
        >
          Close
        </button>
      </div>

      {loading && (
        <div className="text-text-muted italic">Resolving {token}…</div>
      )}
      {error && <div className="text-red-400">Error: {error}</div>}

      {resp && resp.primary && (
        <div className="space-y-3">
          <div>
            <div className="text-text-muted uppercase tracking-wide text-[10px] mb-1">
              Primary
            </div>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
              {Object.entries(resp.primary).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-text-muted font-mono text-[10px]">
                    {k}
                  </dt>
                  <dd className="text-text-primary whitespace-pre-wrap break-words">
                    {v == null
                      ? <span className="text-text-muted italic">null</span>
                      : typeof v === 'object'
                        ? <span className="text-text-muted font-mono text-[10px]">{JSON.stringify(v)}</span>
                        : String(v)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          {resp.tags && resp.tags.length > 0 && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-1">
                Tags
              </div>
              <div className="flex flex-wrap gap-1">
                {resp.tags.map((t) => (
                  <span
                    key={t}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-surface-tertiary text-text-secondary"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {resp.context && Object.keys(resp.context).length > 0 && (
            <details className="text-text-secondary">
              <summary className="cursor-pointer text-text-muted uppercase tracking-wide text-[10px]">
                Context ({Object.keys(resp.context).length} keys)
              </summary>
              <pre className="mt-1 text-[10px] font-mono whitespace-pre-wrap break-words bg-surface-tertiary/40 p-2 rounded max-h-60 overflow-y-auto">
                {JSON.stringify(resp.context, null, 2)}
              </pre>
            </details>
          )}

          {resp.next_tokens && resp.next_tokens.length > 0 && (
            <div>
              <div className="text-text-muted uppercase tracking-wide text-[10px] mb-1">
                Drill into ({resp.next_tokens.length})
              </div>
              <ul className="space-y-1">
                {resp.next_tokens.map((nt, i) => (
                  <li
                    key={`${nt.token}-${i}`}
                    className="flex items-baseline gap-2"
                  >
                    <TokenChip token={nt.token} onClick={onNavigate} />
                    <span className="text-text-secondary">{nt.label}</span>
                    {nt.kind && (
                      <span className="text-[10px] text-text-muted italic">
                        {nt.kind}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function WorkingMemoryView({
  wm,
  expandedToken,
  onTokenClick,
  onCloseDetail,
}: {
  wm: WorkingMemory
  expandedToken: string | null
  onTokenClick: (token: string) => void
  onCloseDetail: () => void
}) {
  return (
    <div className="space-y-4 text-xs">
      {expandedToken && (
        <DetailCard
          token={expandedToken}
          onNavigate={onTokenClick}
          onClose={onCloseDetail}
        />
      )}
      {((wm as any).relevant_context?.length ?? 0) > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Relevant From The Whole Corpus (
            {(wm as any).relevant_context.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (semantic neighbors of the active questions/projects —
              older material recency can't reach)
            </span>
          </div>
          <ul className="space-y-1.5">
            {(wm as any).relevant_context.map(
              (r: { gist_id: number; token: string; similarity: number;
                    relevant_to: string; snippet: string;
                    created_at?: string }) => (
                <li key={r.gist_id} className="flex items-baseline gap-2">
                  <TokenChip token={r.token} onClick={onTokenClick} />
                  <span className="text-text-secondary flex-1">
                    {r.snippet}
                  </span>
                  <span className="text-[10px] text-accent shrink-0"
                        title={`similarity ${r.similarity}`}>
                    {r.relevant_to.length > 24
                      ? r.relevant_to.slice(0, 24) + '…'
                      : r.relevant_to}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      )}

      {wm.top_projects && wm.top_projects.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Top Projects ({wm.top_projects.length})
          </div>
          <ul className="space-y-1">
            {wm.top_projects.map((p) => (
              <li key={p.tag} className="flex justify-between">
                <span className="text-text-primary">{p.name || p.tag}</span>
                <span className="text-text-muted">
                  {p.last_touched?.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.open_todos && wm.open_todos.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Open Reminders ({wm.open_todos.length})
          </div>
          <ul className="space-y-1 text-text-secondary">
            {wm.open_todos.slice(0, 8).map((t) => (
              <li key={t.id} className="truncate">
                • {t.content}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Slice 3f.5 #2: question-centered primary view (with evidence) */}
      {wm.top_questions && wm.top_questions.length > 0 ? (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Open Questions ({wm.top_questions.length}) — primary axis
          </div>
          <ul className="space-y-3">
            {wm.top_questions.map((q) => (
              <li key={q.id} className="border-l-2 border-border pl-3">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <TokenChip
                    token={q.token}
                    active={expandedToken === q.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-text-muted text-[10px] uppercase">
                    [{q.confidence} · {q.lifecycle} · {q.evidence_count}ev]
                  </span>
                  <span className="text-text-primary font-medium">
                    {q.question}
                  </span>
                </div>
                {q.recent_evidence && q.recent_evidence.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {q.recent_evidence.slice(0, 3).map((ev, i) => {
                      const body =
                        ev.evidence_body || ev.reason || '(no body)'
                      const contribColor =
                        ev.contribution === 'complicates'
                          ? 'text-amber-400'
                          : ev.contribution === 'reframes'
                            ? 'text-accent-hover'
                            : ev.contribution === 'answers'
                              ? 'text-success'
                              : 'text-text-muted'
                      return (
                        <li
                          key={`${q.id}-${i}`}
                          className="text-[11px] text-text-secondary flex items-baseline gap-1.5 flex-wrap"
                        >
                          <TokenChip
                            token={ev.token}
                            active={expandedToken === ev.token}
                            onClick={onTokenClick}
                          />
                          <span className={contribColor}>
                            ◆ [{ev.contribution}]
                          </span>
                          <span>
                            {body.length > 200
                              ? body.slice(0, 200) + '…'
                              : body}
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : wm.open_questions && wm.open_questions.length > 0 ? (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Open Questions ({wm.open_questions.length})
          </div>
          <ul className="space-y-1 text-text-secondary">
            {wm.open_questions.map((q) => (
              <li key={q.id}>
                <span className="text-text-muted text-[10px] mr-1">
                  [{q.confidence}]
                </span>
                {q.question}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {((wm as any).recent_decisions?.length ?? 0) > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Decisions ({(wm as any).recent_decisions.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (human judgment captured by the loop + looper)
            </span>
          </div>
          <ul className="space-y-1.5">
            {(wm as any).recent_decisions.slice(0, 8).map(
              (d: { id: number; content: string; project?: string }) => (
                <li key={d.id} className="flex items-baseline gap-2">
                  <span className="text-text-secondary flex-1">
                    {d.content.length > 180
                      ? d.content.slice(0, 180) + '…'
                      : d.content}
                  </span>
                  {d.project && (
                    <span className="text-[10px] text-accent shrink-0">
                      {d.project}
                    </span>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}

      {wm.unfiled_recent_gists && wm.unfiled_recent_gists.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Unfiled Recent Gists ({wm.unfiled_recent_gists.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (didn't route to any open question — possibly a new
              question forming)
            </span>
          </div>
          <ul className="space-y-1 text-text-secondary text-[11px]">
            {wm.unfiled_recent_gists.slice(0, 5).map((g) => (
              <li
                key={g.id}
                className="flex items-baseline gap-1.5 flex-wrap"
              >
                <TokenChip
                  token={g.token}
                  active={expandedToken === g.token}
                  onClick={onTokenClick}
                />
                <span>
                  {g.body.length > 200 ? g.body.slice(0, 200) + '…' : g.body}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_themes && wm.recent_themes.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Themes ({wm.recent_themes.length})
          </div>
          <ul className="space-y-1 text-text-secondary">
            {wm.recent_themes.map((t) => (
              <li key={t.id} className="flex items-baseline gap-1.5">
                <TokenChip
                  token={t.token}
                  active={expandedToken === t.token}
                  onClick={onTokenClick}
                />
                <span>{t.title}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_episode_titles && wm.recent_episode_titles.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Episodes ({wm.recent_episode_titles.length})
          </div>
          <div className="text-text-secondary text-[11px]">
            {wm.recent_episode_titles.join(' · ')}
          </div>
        </div>
      )}

      {wm.last_week_digest && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Last Week Digest
          </div>
          <p className="text-text-secondary leading-relaxed">
            {wm.last_week_digest}
          </p>
        </div>
      )}

      {/* Slice 3g: depth — patterns / drift / institutional notes / rollups */}

      {wm.recent_patterns && wm.recent_patterns.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Patterns ({wm.recent_patterns.length})
          </div>
          <ul className="space-y-1.5">
            {wm.recent_patterns.map((p) => (
              <li key={p.id} className="border-l-2 border-border pl-3">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <TokenChip
                    token={p.token}
                    active={expandedToken === p.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-text-muted text-[10px] uppercase">
                    [{p.confidence || '?'} · {p.occurrences ?? 1}×]
                  </span>
                  <span className="text-text-primary font-medium">
                    {p.name}
                  </span>
                </div>
                {p.body && (
                  <p className="text-[11px] text-text-secondary mt-0.5">
                    {p.body}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_drift && wm.recent_drift.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Drift ({wm.recent_drift.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (started / stopped / shifted — what's changing)
            </span>
          </div>
          <ul className="space-y-1">
            {wm.recent_drift.map((d) => {
              const dirColor =
                d.direction === 'started'
                  ? 'text-success'
                  : d.direction === 'stopped'
                    ? 'text-amber-400'
                    : d.direction === 'shifted'
                      ? 'text-accent-hover'
                      : 'text-text-muted'
              return (
                <li
                  key={d.id}
                  className="text-[11px] text-text-secondary flex items-baseline gap-2 flex-wrap"
                >
                  <TokenChip
                    token={d.token}
                    active={expandedToken === d.token}
                    onClick={onTokenClick}
                  />
                  <span className={`text-[10px] uppercase ${dirColor}`}>
                    {d.direction || '—'}
                  </span>
                  <span>{d.body}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {wm.recent_future_notes && wm.recent_future_notes.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Notes to Future Overseer ({wm.recent_future_notes.length}
            {wm.future_overseer_notes_count &&
              wm.future_overseer_notes_count > wm.recent_future_notes.length &&
              ` of ${wm.future_overseer_notes_count}`}
            )
            <span className="ml-2 normal-case text-[10px] italic">
              (institutional memory — what prior instances laid down)
            </span>
          </div>
          <ul className="space-y-1.5">
            {wm.recent_future_notes.map((n) => (
              <li key={n.id} className="border-l-2 border-border pl-3">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <TokenChip
                    token={n.token}
                    active={expandedToken === n.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-[10px] text-text-muted uppercase">
                    {n.instance_id}
                    {n.written_at && (
                      <span className="ml-2 normal-case" title={fmtTime((n as any).local_written_at, n.written_at)}>
                        {fmtTime((n as any).local_written_at, n.written_at)}
                      </span>
                    )}
                  </span>
                </div>
                <p className="text-[11px] text-text-secondary mt-0.5">
                  {n.body}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {wm.recent_rollups && wm.recent_rollups.length > 0 && (
        <div>
          <div className="text-text-muted uppercase tracking-wide mb-1">
            Recent Rollups ({wm.recent_rollups.length})
            <span className="ml-2 normal-case text-[10px] italic">
              (per-project per-day automation digest)
            </span>
          </div>
          <ul className="space-y-1.5">
            {wm.recent_rollups.map((r) => (
              <li
                key={`${r.project}-${r.rollup_date}`}
                className="border-l-2 border-border pl-3"
              >
                <div className="flex items-baseline gap-2 text-[10px] text-text-muted uppercase flex-wrap">
                  <TokenChip
                    token={r.token}
                    active={expandedToken === r.token}
                    onClick={onTokenClick}
                  />
                  <span className="text-text-primary normal-case font-medium">
                    {r.project}
                  </span>
                  <span>{r.rollup_date}</span>
                  {r.session_count != null && (
                    <span>· {r.session_count} sess</span>
                  )}
                  {r.median_minutes != null && (
                    <span>· {r.median_minutes}min median</span>
                  )}
                </div>
                <p className="text-[11px] text-text-secondary mt-0.5">
                  {r.summary}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

