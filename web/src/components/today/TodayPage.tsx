import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { apiFetch } from '../../lib/api'
import { SectionLabel } from '../ui'

// The projects table renders poorly without a gfm plugin, and the prose brief
// is the scannable part, so drop table rows and collapse the blanks left behind.
function proseOnly(md: string): string {
  return md
    .split('\n')
    .filter((l) => !/^\s*\|/.test(l))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** Today: the glance. Mirrors the phone's Home screen (2026-07-02 UX spec) -
 * a quiet, scannable landing with a single accent fill (the Talk pill), then
 * the overseer's briefing. The agenda + day-grouped feed + interactive Bell
 * come in a later slice; for now the briefing is the corpus glance that the
 * /intro endpoint already assembles. */
export function TodayPage() {
  const [brief, setBrief] = useState<string | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    apiFetch<{ markdown?: string }>('/overseer/intro?format=markdown')
      .then((r) => setBrief(proseOnly(r.markdown ?? '')))
      .catch(() => setError(true))
  }, [])

  const hour = new Date().getHours()
  const greeting =
    hour < 5 ? 'Still up' : hour < 12 ? 'Good morning'
    : hour < 18 ? 'Good afternoon' : 'Good evening'
  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long', month: 'long', day: 'numeric',
  })

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">{greeting}</h1>
          <p className="text-sm text-text-muted mt-0.5">{today}</p>
        </div>

        {/* The single accent fill (mirrors the phone's Talk pill). */}
        <button
          onClick={() => { window.location.hash = '/chat' }}
          className="w-full flex items-center justify-center gap-2 px-5 py-3.5 rounded-2xl bg-accent text-white font-medium hover:bg-accent-hover transition-colors cursor-pointer"
        >
          <span className="text-lg">💬</span>
          Talk to Cortex
        </button>

        <div>
          <SectionLabel>Your briefing</SectionLabel>
          <div className="bg-surface rounded-xl border border-border p-6 text-sm text-text-secondary leading-relaxed [&_h1]:text-lg [&_h1]:font-bold [&_h1]:text-text-primary [&_h1]:mb-2 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-text-primary [&_h2]:mt-4 [&_h2]:mb-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:space-y-1 [&_li]:marker:text-text-muted [&_strong]:text-text-primary [&_a]:text-accent-hover [&_em]:text-text-muted">
            {error ? (
              <p className="text-text-muted">Could not load your briefing.</p>
            ) : brief === null ? (
              <p className="text-text-muted">Loading...</p>
            ) : brief === '' ? (
              <p className="text-text-muted">
                Your corpus is quiet. Connect an AI or start a chat to fill it.
              </p>
            ) : (
              <ReactMarkdown>{brief}</ReactMarkdown>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
