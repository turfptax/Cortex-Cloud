import { useEffect, useState, useCallback, type ReactNode } from 'react'
import { apiFetch } from '../../../lib/api'

// People taxonomy CP3 (2026-06-13): the dedicated Contacts panel over
// the canonical overseer_people store. Full list/search + edit + the
// structured person_notes channel (provenance + modality + note_kind —
// the locked taxonomy axes). This is the surface Tory uses to clean up
// and enrich contacts; the Data-tab explorer is cortex.db-only and does
// NOT see these 193 rows.

interface Person {
  id: number
  name: string
  display_name?: string
  aliases?: string[]
  tags?: string[]
  areas_of_expertise?: string[]
  online_handles?: string[]
  social_links?: string[]
  notes?: string
  last_interacted_at?: string | null
  created_by_agent?: string
  linked_projects?: { project: string; role?: string }[]
}

interface PersonNote {
  id: number
  person_id: number
  body: string
  provenance: string
  modality: string
  note_kind: string
  created_at: string
  local_created_at?: string | null
  created_by_agent?: string
}

interface PeopleStats {
  total_people: number
  orphans_count: number
  multi_project_count: number
  added_7d: number
}

const NOTE_KINDS = ['context', 'interaction', 'preference', 'commitment', 'fact']
const PROVENANCES = ['tory-typed', 'tory-voice', 'overseer', 'ai-convo', 'import']
const MODALITIES = [
  'statement', 'observation', 'inference', 'hypothesis',
  'value-judgment', 'external-claim', 'pattern',
]

function csv(arr?: string[]): string {
  return (arr || []).join(', ')
}
function splitCsv(s: string): string[] {
  return s.split(',').map((x) => x.trim()).filter(Boolean)
}

export function ContactsPanel() {
  const [people, setPeople] = useState<Person[]>([])
  const [stats, setStats] = useState<PeopleStats | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<Person | null>(null)
  const [notes, setNotes] = useState<PersonNote[]>([])
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')

  // edit form
  const [fDisplay, setFDisplay] = useState('')
  const [fAliases, setFAliases] = useState('')
  const [fTags, setFTags] = useState('')
  const [fExpertise, setFExpertise] = useState('')
  const [fNotes, setFNotes] = useState('')

  // new-note form
  const [nBody, setNBody] = useState('')
  const [nKind, setNKind] = useState('context')
  const [nProv, setNProv] = useState('tory-typed')
  const [nMod, setNMod] = useState('statement')

  const loadList = useCallback(async (q: string) => {
    setLoading(true)
    setError(null)
    try {
      const path = q.trim()
        ? `/overseer/people/search?q=${encodeURIComponent(q.trim())}&limit=200`
        : '/overseer/people?limit=200&order_by=name'
      const r = await apiFetch<{ ok: boolean; people: Person[] }>(path)
      setPeople(r.people || [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadList('')
    apiFetch<{ ok: boolean } & PeopleStats>('/overseer/people/stats')
      .then((s) => setStats(s)).catch(() => {})
  }, [loadList])

  const loadDetail = useCallback(async (id: number) => {
    setSavedMsg('')
    try {
      const [pr, nr] = await Promise.all([
        apiFetch<{ ok: boolean; person: Person }>(
          `/overseer/people/get?id=${id}`),
        apiFetch<{ ok: boolean; notes: PersonNote[] }>(
          `/overseer/people/notes?person_id=${id}`),
      ])
      setDetail(pr.person)
      setNotes(nr.notes || [])
      setFDisplay(pr.person.display_name || '')
      setFAliases(csv(pr.person.aliases))
      setFTags(csv(pr.person.tags))
      setFExpertise(csv(pr.person.areas_of_expertise))
      setFNotes(pr.person.notes || '')
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => {
    if (selectedId != null) loadDetail(selectedId)
  }, [selectedId, loadDetail])

  async function saveEdits() {
    if (!detail) return
    setSaving(true)
    setSavedMsg('')
    try {
      await apiFetch('/overseer/people/update', {
        method: 'POST',
        body: JSON.stringify({
          id: detail.id,
          display_name: fDisplay,
          aliases: splitCsv(fAliases),
          tags: splitCsv(fTags),
          areas_of_expertise: splitCsv(fExpertise),
          notes_replace: fNotes,
        }),
      })
      setSavedMsg('Saved')
      await loadDetail(detail.id)
      loadList(query)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function addNote() {
    if (!detail || !nBody.trim()) return
    setSaving(true)
    try {
      await apiFetch('/overseer/people/notes/add', {
        method: 'POST',
        body: JSON.stringify({
          person_id: detail.id,
          body: nBody.trim(),
          note_kind: nKind,
          provenance: nProv,
          modality: nMod,
          created_by_agent: 'hub-contacts',
        }),
      })
      setNBody('')
      const nr = await apiFetch<{ ok: boolean; notes: PersonNote[] }>(
        `/overseer/people/notes?person_id=${detail.id}`)
      setNotes(nr.notes || [])
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function deleteNote(noteId: number) {
    if (!detail) return
    try {
      await apiFetch('/overseer/people/notes/delete', {
        method: 'POST',
        body: JSON.stringify({ note_id: noteId }),
      })
      setNotes((ns) => ns.filter((n) => n.id !== noteId))
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="flex flex-col gap-3 h-[calc(100vh-180px)]">
      {/* Stats header — aids triage / cleanup */}
      {stats && (
        <div className="flex items-center gap-2 text-xs">
          <StatChip label="contacts" value={stats.total_people} />
          <StatChip label="unlinked" value={stats.orphans_count}
            hint="no project links — prune candidates" />
          <StatChip label="connectors" value={stats.multi_project_count}
            hint="linked to 2+ projects" />
          <StatChip label="added 7d" value={stats.added_7d} />
          <span className="ml-auto text-text-muted">
            canonical store · overseer_people
          </span>
        </div>
      )}

      <div className="flex gap-4 flex-1 min-h-0">
      {/* Left: list */}
      <div className="w-72 shrink-0 flex flex-col bg-surface-secondary rounded-lg border border-border">
        <div className="p-2 border-b border-border">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') loadList(query) }}
            placeholder="Search contacts..."
            className="w-full px-2 py-1.5 text-sm bg-surface-tertiary rounded-md text-text-primary placeholder:text-text-muted outline-none"
          />
          <div className="mt-1 text-xs text-text-muted">
            {loading ? 'Loading...' : `${people.length} contacts`}
          </div>
        </div>
        <div className="overflow-y-auto flex-1">
          {people.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedId(p.id)}
              className={`w-full text-left px-3 py-2 border-b border-border/50 cursor-pointer transition-colors ${
                selectedId === p.id
                  ? 'bg-accent/15'
                  : 'hover:bg-surface-tertiary'
              }`}
            >
              <div className="text-sm text-text-primary truncate">
                {p.display_name || p.name}
              </div>
              {p.display_name && p.display_name !== p.name && (
                <div className="text-xs text-text-muted truncate">{p.name}</div>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Right: detail */}
      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="mb-2 text-xs text-red-400">{error}</div>
        )}
        {!detail && (
          <div className="text-sm text-text-muted p-4">
            Select a contact to view and edit. Contacts live in the
            canonical overseer_people store.
          </div>
        )}
        {detail && (
          <div className="space-y-4">
            <div className="bg-surface-secondary rounded-lg border border-border p-4">
              <div className="flex items-baseline justify-between">
                <h3 className="text-lg font-semibold text-text-primary">
                  {detail.name}
                </h3>
                <span className="text-xs text-text-muted">
                  id {detail.id}
                  {detail.created_by_agent
                    ? ` · ${detail.created_by_agent}` : ''}
                </span>
              </div>
              <div className="text-xs text-text-muted mb-3">
                name is the canonical key (not editable here); use
                display name + aliases for how you refer to them
              </div>

              <div className="grid grid-cols-1 gap-3">
                <Field label="Display name">
                  <input value={fDisplay}
                    onChange={(e) => setFDisplay(e.target.value)}
                    className={inputCls} />
                </Field>
                <Field label="Aliases / nicknames (comma-separated)">
                  <input value={fAliases}
                    onChange={(e) => setFAliases(e.target.value)}
                    placeholder="Dave, D, Korkoian"
                    className={inputCls} />
                </Field>
                <Field label="Tags (comma-separated)">
                  <input value={fTags}
                    onChange={(e) => setFTags(e.target.value)}
                    className={inputCls} />
                </Field>
                <Field label="Areas of expertise (comma-separated)">
                  <input value={fExpertise}
                    onChange={(e) => setFExpertise(e.target.value)}
                    className={inputCls} />
                </Field>
                <Field label="Notes (free-form)">
                  <textarea value={fNotes}
                    onChange={(e) => setFNotes(e.target.value)}
                    rows={4}
                    className={inputCls + ' resize-y'} />
                </Field>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <button onClick={saveEdits} disabled={saving}
                  className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50">
                  {saving ? 'Saving...' : 'Save changes'}
                </button>
                {savedMsg && (
                  <span className="text-xs text-success">{savedMsg}</span>
                )}
              </div>
            </div>

            {/* Structured notes (the taxonomy channel) */}
            <div className="bg-surface-secondary rounded-lg border border-border p-4">
              <h4 className="text-sm font-semibold text-text-primary mb-2">
                Structured notes
                <span className="ml-2 text-xs font-normal text-text-muted">
                  provenance + modality + kind (taxonomy-tagged)
                </span>
              </h4>

              <div className="bg-surface-tertiary rounded-md p-2 mb-3">
                <textarea value={nBody}
                  onChange={(e) => setNBody(e.target.value)}
                  rows={2}
                  placeholder="Add context, a preference, an interaction, a commitment..."
                  className={inputCls + ' resize-y mb-2'} />
                <div className="flex flex-wrap items-center gap-2">
                  <Select label="kind" value={nKind} setValue={setNKind} opts={NOTE_KINDS} />
                  <Select label="who" value={nProv} setValue={setNProv} opts={PROVENANCES} />
                  <Select label="type" value={nMod} setValue={setNMod} opts={MODALITIES} />
                  <button onClick={addNote} disabled={saving || !nBody.trim()}
                    className="ml-auto px-3 py-1 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50">
                    Add note
                  </button>
                </div>
              </div>

              {notes.length === 0 && (
                <div className="text-xs text-text-muted">No structured notes yet.</div>
              )}
              <div className="space-y-2">
                {notes.map((n) => (
                  <div key={n.id}
                    className="bg-surface-tertiary rounded-md p-2 group">
                    <div className="text-sm text-text-primary">{n.body}</div>
                    <div className="mt-1 flex items-center gap-1.5 text-xs text-text-muted">
                      <Pill>{n.note_kind}</Pill>
                      <Pill>{n.provenance}</Pill>
                      <Pill>{n.modality}</Pill>
                      <span className="ml-1">
                        {(n.local_created_at || n.created_at || '').slice(0, 16).replace('T', ' ')}
                      </span>
                      <button onClick={() => deleteNote(n.id)}
                        className="ml-auto opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-300 cursor-pointer transition-opacity">
                        delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  )
}

const inputCls =
  'w-full px-2 py-1.5 text-sm bg-surface-tertiary rounded-md text-text-primary placeholder:text-text-muted outline-none border border-border focus:border-accent'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-text-secondary mb-1">{label}</span>
      {children}
    </label>
  )
}

function Select({ label, value, setValue, opts }: {
  label: string; value: string; setValue: (v: string) => void; opts: string[]
}) {
  return (
    <label className="flex items-center gap-1 text-xs text-text-muted">
      <span>{label}</span>
      <select value={value} onChange={(e) => setValue(e.target.value)}
        className="bg-surface-secondary border border-border rounded px-1 py-0.5 text-text-primary outline-none cursor-pointer">
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  )
}

function Pill({ children }: { children: ReactNode }) {
  return (
    <span className="px-1.5 py-0.5 rounded bg-surface-secondary border border-border text-text-secondary">
      {children}
    </span>
  )
}

function StatChip({ label, value, hint }: {
  label: string; value: number; hint?: string
}) {
  return (
    <span title={hint}
      className="inline-flex items-baseline gap-1 px-2 py-1 rounded-md bg-surface-secondary border border-border">
      <span className="font-semibold text-text-primary">{value}</span>
      <span className="text-text-muted">{label}</span>
    </span>
  )
}
