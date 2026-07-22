import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../../lib/api'
import { Badge, Card } from '../ui'

/** UI redesign Phase 1 (2026-06-10): the Hub's new front door.
 * Omnibar over the corpus, semantic results from the Pi's vector
 * index, click-through into the existing detail-token surface. */

interface VectorHit {
  token: string
  gist_id: number
  similarity: number
  period_label?: string
  confidence?: string
  created_at?: string
  snippet: string
}

interface VectorSearchResp {
  ok: boolean
  q?: string
  count?: number
  knn_ms?: number
  results?: VectorHit[]
  error?: string
}

interface VectorStatusResp {
  ok: boolean
  available?: boolean
  total_gists?: number
  coverage_pct?: number
  error?: string
}

interface OverseerStatusResp {
  ok: boolean
  overseer_db?: Record<string, number>
  loop_running?: boolean
  error?: string
}

interface DetailNextToken {
  token: string
  label: string
}

interface DetailResp {
  ok: boolean
  token?: string
  type?: string
  primary?: Record<string, unknown>
  next_tokens?: DetailNextToken[]
  error?: string
}

export function SearchPage() {
  const [input, setInput] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [detailToken, setDetailToken] = useState('')

  const search = useQuery({
    queryKey: ['vector-search', submitted],
    queryFn: () =>
      apiFetch<VectorSearchResp>(
        `/overseer/vector/search?q=${encodeURIComponent(submitted)}&k=10`),
    enabled: !!submitted,
    staleTime: 5 * 60_000,
  })

  const vecStatus = useQuery({
    queryKey: ['vector-status'],
    queryFn: () => apiFetch<VectorStatusResp>('/overseer/vector/status'),
    staleTime: 60_000,
  })

  const overseer = useQuery({
    queryKey: ['overseer-status'],
    queryFn: () => apiFetch<OverseerStatusResp>('/overseer/status'),
    staleTime: 60_000,
  })

  const detail = useQuery({
    queryKey: ['detail', detailToken],
    queryFn: () =>
      apiFetch<DetailResp>(
        `/overseer/detail?token=${encodeURIComponent(detailToken)}`),
    enabled: !!detailToken,
    staleTime: 5 * 60_000,
  })

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    setDetailToken('')
    const q = input.trim()
    if (q && q === submitted) {
      // Same query resubmitted: force a refetch. Without this, a
      // search that failed (e.g. Pi mid-boot) stays cached as an
      // error and pressing Enter again appears to do nothing.
      search.refetch()
    } else {
      setSubmitted(q)
    }
  }

  const db = overseer.data?.overseer_db ?? {}
  const results = search.data?.results ?? []
  const primary = detail.data?.primary

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto w-full p-6">
        <form onSubmit={onSubmit} className="mb-5">
          <div className="flex items-center gap-3 bg-surface-secondary border border-border rounded-xl px-4 py-3 focus-within:border-accent transition-colors">
            <span className="text-text-muted text-lg">🔍</span>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask your corpus anything…"
              className="flex-1 bg-transparent outline-none text-base text-text-primary placeholder:text-text-muted"
              autoFocus
            />
            {search.data?.knn_ms !== undefined && (
              <span className="text-xs text-text-muted shrink-0">
                semantic · {search.data.knn_ms}ms
              </span>
            )}
          </div>
        </form>

        {search.isFetching && (
          <p className="text-sm text-text-muted mb-4">Searching…</p>
        )}
        {search.data && !search.data.ok && (
          <p className="text-sm text-danger mb-4">
            Search failed: {search.data.error}
          </p>
        )}

        {results.length > 0 && (
          <div className="space-y-2 mb-6">
            {results.map((r) => (
              <button
                key={r.token}
                onClick={() => setDetailToken(r.token)}
                className={`w-full text-left bg-surface-secondary border rounded-lg px-4 py-3 transition-colors cursor-pointer hover:border-accent ${
                  detailToken === r.token ? 'border-accent' : 'border-border'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Badge tone="accent" className="font-mono">
                    {r.token}
                  </Badge>
                  <span className="text-xs text-text-muted">
                    {(r.similarity * 100).toFixed(0)}% match
                    {r.period_label ? ` · ${r.period_label}` : ''}
                  </span>
                </div>
                <p className="text-sm text-text-primary m-0">{r.snippet}</p>
              </button>
            ))}
          </div>
        )}

        {detailToken && (
          <Card
            title={detailToken}
            actions={
              <button
                onClick={() => setDetailToken('')}
                className="text-text-muted hover:text-text-primary text-sm cursor-pointer"
              >
                ✕
              </button>
            }
            className="mb-6"
          >
            {detail.isFetching && (
              <p className="text-sm text-text-muted">Loading…</p>
            )}
            {primary && (
              <>
                <p className="text-sm text-text-primary whitespace-pre-wrap">
                  {String(
                    primary.body ?? primary.question ?? primary.narrative ??
                    primary.summary ?? JSON.stringify(primary, null, 2))}
                </p>
                {(detail.data?.next_tokens?.length ?? 0) > 0 && (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {detail.data!.next_tokens!.slice(0, 8).map((t) => (
                      <button
                        key={t.token}
                        onClick={() => setDetailToken(t.token)}
                        className="cursor-pointer"
                        title={t.label}
                      >
                        <Badge tone="neutral" className="font-mono hover:text-accent">
                          {t.token}
                        </Badge>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
            {detail.data && !detail.data.ok && (
              <p className="text-sm text-danger">{detail.data.error}</p>
            )}
          </Card>
        )}

        {!submitted && (
          <p className="text-sm text-text-muted mb-5">
            Meaning-search across {vecStatus.data?.total_gists?.toLocaleString() ?? '…'}{' '}
            gists of your history. Try "what did I decide about the pet
            plugin" or "times I felt stuck on hardware".
          </p>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Metric label="Gists" value={db.summaries_gist} />
          <Metric
            label="Vector coverage"
            value={
              vecStatus.data?.coverage_pct !== undefined
                ? `${vecStatus.data.coverage_pct}%`
                : undefined
            }
          />
          <Metric label="Open questions" value={db.open_questions} />
          <Metric label="Themes" value={db.summaries_theme} />
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value?: number | string }) {
  return (
    <div className="bg-surface-secondary rounded-lg px-4 py-3">
      <p className="text-xs text-text-secondary m-0 mb-0.5">{label}</p>
      <p className="text-xl font-semibold text-text-primary m-0">
        {value ?? '—'}
      </p>
    </div>
  )
}
