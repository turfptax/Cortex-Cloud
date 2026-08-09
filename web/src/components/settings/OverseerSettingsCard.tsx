import { useEffect, useMemo, useState } from 'react'
import { apiFetch, ApiError } from '../../lib/api'
import { Button, Card, SectionLabel } from '../ui'

/** The overseer's dials, live from the instance.
 *
 * Until now the model roster and the loop budgets were constants in
 * plugin.toml, which ships with the public repo: changing the brain
 * meant a commit and a redeploy, and every install carried the
 * reference instance's picks. This card edits the per-instance
 * overlay instead (stored in the corpus DB, never in git). Model picks
 * bind on the next LLM call, loop dials on the next tick. */

interface SettingEntry {
  key: string
  section: string
  type: 'model' | 'model_map' | 'int' | 'number' | 'bool' | 'string_list'
  label: string
  help: string
  default: unknown
  override: unknown
  effective: unknown
  min?: number
  max?: number
}

interface CatalogModel {
  id: string
  name: string
  context_length: number
  prompt_usd_per_m: number
  completion_usd_per_m: number
}

/** Drafts hold what the inputs hold: strings for numerics and lists
 * (textarea), a full purpose->model map for the override table. */
type Draft = Record<string, unknown>

const INPUT_CLS =
  'w-full px-3 py-2 rounded-lg bg-surface-secondary border border-border ' +
  'text-sm text-text-primary placeholder:text-text-muted ' +
  'focus:outline-none focus:border-accent/60'

function draftFrom(entries: SettingEntry[]): Draft {
  const d: Draft = {}
  for (const e of entries) {
    if (e.type === 'model') {
      d[e.key] = (e.effective as string) ?? ''
    } else if (e.type === 'model_map') {
      d[e.key] = { ...((e.effective as Record<string, string>) ?? {}) }
    } else if (e.type === 'bool') {
      d[e.key] = Boolean(e.effective)
    } else if (e.type === 'string_list') {
      d[e.key] = ((e.effective as string[]) ?? []).join('\n')
    } else {
      d[e.key] = e.effective == null ? '' : String(e.effective)
    }
  }
  return d
}

/** Parse one draft value back to its API type. Throws a message meant
 * for the human when the text does not parse. */
function parseDraft(e: SettingEntry, raw: unknown): unknown {
  if (e.type === 'model') return String(raw ?? '').trim()
  if (e.type === 'bool') return Boolean(raw)
  if (e.type === 'model_map') {
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(
      (raw as Record<string, string>) ?? {})) {
      const m = (v ?? '').trim()
      if (m) out[k] = m
    }
    return out
  }
  if (e.type === 'string_list') {
    return String(raw ?? '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
  }
  const text = String(raw ?? '').trim()
  if (!text) throw new Error(`${e.label}: enter a value`)
  const n = Number(text)
  if (!Number.isFinite(n)) throw new Error(`${e.label}: not a number`)
  if (e.type === 'int' && !Number.isInteger(n)) {
    throw new Error(`${e.label}: must be a whole number`)
  }
  if (e.min != null && n < e.min) {
    throw new Error(`${e.label}: minimum is ${e.min}`)
  }
  if (e.max != null && n > e.max) {
    throw new Error(`${e.label}: maximum is ${e.max}`)
  }
  return n
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}

function fmtContext(n: number): string {
  if (!n) return ''
  return n >= 1000 ? `${Math.round(n / 1000)}k ctx` : `${n} ctx`
}

function fmtPrice(m: CatalogModel): string {
  return `$${m.prompt_usd_per_m}/M in, $${m.completion_usd_per_m}/M out`
}

/** Text input with a filtered dropdown over the OpenRouter catalog.
 * Free text is always allowed: the catalog is a convenience, not a
 * gate, so a brand-new model id works the day it ships. */
function ModelPicker({
  value,
  onChange,
  models,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  models: CatalogModel[]
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const matches = useMemo(() => {
    const q = value.trim().toLowerCase()
    if (!q) return models.slice(0, 12)
    return models
      .filter(
        (m) =>
          m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)
      )
      .slice(0, 12)
  }, [value, models])

  return (
    <div className="relative">
      <input
        value={value}
        onChange={(ev) => {
          onChange(ev.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        placeholder={placeholder ?? 'provider/model-id'}
        className={INPUT_CLS + ' font-mono'}
        spellCheck={false}
      />
      {open && matches.length > 0 && (
        <ul className="absolute z-20 mt-1 w-full max-h-64 overflow-y-auto bg-surface-tertiary border border-border rounded-lg shadow-lg">
          {matches.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                // onMouseDown so the pick lands before the input's blur
                // closes the list.
                onMouseDown={(ev) => {
                  ev.preventDefault()
                  onChange(m.id)
                  setOpen(false)
                }}
                className="w-full text-left px-3 py-2 hover:bg-surface-secondary transition-colors"
              >
                <span className="block text-sm text-text-primary font-mono">
                  {m.id}
                </span>
                <span className="block text-[11px] text-text-muted">
                  {m.name}
                  {m.context_length ? ` · ${fmtContext(m.context_length)}` : ''}
                  {` · ${fmtPrice(m)}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Label row + optional "custom" marker and reset-to-default link. */
function KeyHeader({
  entry,
  isDefault,
  onReset,
}: {
  entry: SettingEntry
  isDefault: boolean
  onReset: () => void
}) {
  return (
    <div className="flex items-center justify-between mb-1">
      <span className="text-xs font-medium text-text-muted">{entry.label}</span>
      {!isDefault && (
        <span className="flex items-center gap-2">
          <span className="text-[11px] text-warning">custom</span>
          <button
            type="button"
            onClick={onReset}
            className="text-[11px] text-text-muted hover:text-text-primary underline cursor-pointer"
          >
            reset to default
          </button>
        </span>
      )}
    </div>
  )
}

export function OverseerSettingsCard() {
  const [entries, setEntries] = useState<SettingEntry[] | null>(null)
  const [draft, setDraft] = useState<Draft>({})
  const [models, setModels] = useState<CatalogModel[]>([])
  const [loadError, setLoadError] = useState('')
  const [saveState, setSaveState] = useState<
    { kind: 'idle' } | { kind: 'saving' } | { kind: 'saved' } | { kind: 'error'; message: string }
  >({ kind: 'idle' })
  const [showTaskModels, setShowTaskModels] = useState(false)

  const load = () => {
    setLoadError('')
    apiFetch<{ ok: boolean; keys: SettingEntry[]; error?: string }>(
      '/overseer/settings'
    )
      .then((resp) => {
        if (!resp.ok) throw new Error(resp.error || 'settings unavailable')
        setEntries(resp.keys)
        setDraft(draftFrom(resp.keys))
      })
      .catch((e) => {
        setLoadError(e instanceof ApiError ? e.userMessage : String(e))
      })
    // The catalog is decoration for the pickers; free text works
    // without it, so a failure here only degrades autocomplete.
    apiFetch<{ ok: boolean; models?: CatalogModel[] }>(
      '/overseer/settings/models'
    )
      .then((resp) => {
        if (resp.ok && resp.models) setModels(resp.models)
      })
      .catch(() => {})
  }

  useEffect(load, [])

  const byKey = useMemo(() => {
    const m: Record<string, SettingEntry> = {}
    for (const e of entries ?? []) m[e.key] = e
    return m
  }, [entries])

  const dirty = useMemo(() => {
    if (!entries) return false
    return entries.some((e) => {
      try {
        return !sameValue(parseDraft(e, draft[e.key]), e.effective)
      } catch {
        return true
      }
    })
  }, [entries, draft])

  const setKey = (key: string, value: unknown) => {
    setDraft((d) => ({ ...d, [key]: value }))
    setSaveState({ kind: 'idle' })
  }

  const resetKey = (e: SettingEntry) => {
    setDraft((d) => ({ ...d, [e.key]: draftFrom([{ ...e, effective: e.default }])[e.key] }))
    setSaveState({ kind: 'idle' })
  }

  const save = async () => {
    if (!entries) return
    const set: Record<string, unknown> = {}
    const reset: string[] = []
    try {
      for (const e of entries) {
        const value = parseDraft(e, draft[e.key])
        if (sameValue(value, e.effective)) continue
        if (sameValue(value, e.default)) {
          if (e.override != null) reset.push(e.key)
          continue
        }
        if (e.type === 'model_map') {
          // Store only the purposes that differ from the manifest.
          const def = (e.default as Record<string, string>) ?? {}
          const partial: Record<string, string> = {}
          for (const [purpose, model] of Object.entries(
            value as Record<string, string>
          )) {
            if (model && model !== def[purpose]) partial[purpose] = model
          }
          if (Object.keys(partial).length === 0) {
            if (e.override != null) reset.push(e.key)
          } else {
            set[e.key] = partial
          }
          continue
        }
        set[e.key] = value
      }
    } catch (err) {
      setSaveState({ kind: 'error', message: String((err as Error).message) })
      return
    }
    if (Object.keys(set).length === 0 && reset.length === 0) {
      setSaveState({ kind: 'saved' })
      return
    }
    setSaveState({ kind: 'saving' })
    try {
      const resp = await apiFetch<{
        ok: boolean
        keys?: SettingEntry[]
        error?: string
      }>('/overseer/settings', {
        method: 'POST',
        body: JSON.stringify({ set, reset }),
      })
      if (!resp.ok || !resp.keys) {
        throw new Error(resp.error || 'save failed')
      }
      setEntries(resp.keys)
      setDraft(draftFrom(resp.keys))
      setSaveState({ kind: 'saved' })
    } catch (err) {
      setSaveState({
        kind: 'error',
        message:
          err instanceof ApiError ? err.userMessage : String((err as Error).message),
      })
    }
  }

  if (loadError) {
    return (
      <Card title="🧠 Overseer">
        <p className="text-sm text-danger mb-3">{loadError}</p>
        <Button variant="secondary" size="sm" onClick={load}>
          Retry
        </Button>
      </Card>
    )
  }
  if (!entries) {
    return (
      <Card title="🧠 Overseer">
        <p className="text-sm text-text-muted">Loading settings…</p>
      </Card>
    )
  }

  const mainModel = byKey['llm.model']
  const taskModels = byKey['llm.model_overrides']
  const loopKeys = entries.filter((e) => e.section === 'loop')
  const ingestKeys = entries.filter((e) => e.section === 'ingest')
  const taskDraft = (draft['llm.model_overrides'] as Record<string, string>) ?? {}
  const taskDefaults = (taskModels?.default as Record<string, string>) ?? {}

  return (
    <Card title="🧠 Overseer">
      <p className="text-xs text-text-muted mb-4">
        Per-instance dials, stored in your corpus (never in the repo).
        Model picks bind on the next reply or tick; loop dials on the
        next tick. No redeploy.
      </p>

      {/* ── The brain ─────────────────────────────────────── */}
      <SectionLabel>Model</SectionLabel>
      {mainModel && (
        <div className="mb-3">
          <KeyHeader
            entry={mainModel}
            isDefault={mainModel.override == null}
            onReset={() => resetKey(mainModel)}
          />
          <ModelPicker
            value={(draft['llm.model'] as string) ?? ''}
            onChange={(v) => setKey('llm.model', v)}
            models={models}
          />
          <p className="text-[11px] text-text-muted mt-1">
            {mainModel.help} Default: {String(mainModel.default)}
          </p>
        </div>
      )}

      {taskModels && (
        <div className="mb-4">
          <button
            type="button"
            onClick={() => setShowTaskModels((s) => !s)}
            className="text-xs text-text-secondary hover:text-text-primary cursor-pointer"
          >
            {showTaskModels ? '▾' : '▸'} Per-task models (
            {Object.keys(taskDefaults).length})
          </button>
          {showTaskModels && (
            <div className="mt-2 space-y-2 border-l-2 border-border pl-3">
              <p className="text-[11px] text-text-muted">{taskModels.help}</p>
              {Object.keys(taskDefaults).map((purpose) => {
                const overridden =
                  (taskDraft[purpose] ?? '') !== (taskDefaults[purpose] ?? '')
                return (
                  <div key={purpose}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-mono text-text-secondary">
                        {purpose}
                      </span>
                      {overridden && (
                        <button
                          type="button"
                          onClick={() =>
                            setKey('llm.model_overrides', {
                              ...taskDraft,
                              [purpose]: taskDefaults[purpose] ?? '',
                            })
                          }
                          className="text-[11px] text-text-muted hover:text-text-primary underline cursor-pointer"
                        >
                          reset
                        </button>
                      )}
                    </div>
                    <ModelPicker
                      value={taskDraft[purpose] ?? ''}
                      onChange={(v) =>
                        setKey('llm.model_overrides', {
                          ...taskDraft,
                          [purpose]: v,
                        })
                      }
                      models={models}
                    />
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Loop + budget ─────────────────────────────────── */}
      <SectionLabel>Loop and budget</SectionLabel>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
        {loopKeys.map((e) =>
          e.type === 'bool' ? (
            <label
              key={e.key}
              className="flex items-center gap-2 text-sm text-text-primary sm:col-span-2"
            >
              <input
                type="checkbox"
                checked={Boolean(draft[e.key])}
                onChange={(ev) => setKey(e.key, ev.target.checked)}
                className="accent-(--color-accent)"
              />
              <span>{e.label}</span>
              {e.override != null && (
                <span className="text-[11px] text-warning">custom</span>
              )}
              {e.help && (
                <span className="text-[11px] text-text-muted">{e.help}</span>
              )}
            </label>
          ) : (
            <div key={e.key}>
              <KeyHeader
                entry={e}
                isDefault={e.override == null}
                onReset={() => resetKey(e)}
              />
              <input
                type="number"
                value={String(draft[e.key] ?? '')}
                onChange={(ev) => setKey(e.key, ev.target.value)}
                min={e.min}
                max={e.max}
                step={e.type === 'int' ? 1 : 0.01}
                className={INPUT_CLS}
              />
              {e.help && (
                <p className="text-[11px] text-text-muted mt-1">{e.help}</p>
              )}
            </div>
          )
        )}
      </div>

      {/* ── Ingest sources ────────────────────────────────── */}
      <SectionLabel>Ingest sources</SectionLabel>
      <div className="space-y-3 mb-4">
        {ingestKeys.map((e) => (
          <div key={e.key}>
            <KeyHeader
              entry={e}
              isDefault={e.override == null}
              onReset={() => resetKey(e)}
            />
            <textarea
              value={String(draft[e.key] ?? '')}
              onChange={(ev) => setKey(e.key, ev.target.value)}
              rows={4}
              spellCheck={false}
              className={INPUT_CLS + ' font-mono resize-y'}
            />
            {e.help && (
              <p className="text-[11px] text-text-muted mt-1">{e.help}</p>
            )}
          </div>
        ))}
      </div>

      {/* ── Save bar ──────────────────────────────────────── */}
      <div className="flex items-center gap-3 pt-3 border-t border-border">
        <Button size="sm" onClick={save} disabled={!dirty || saveState.kind === 'saving'}>
          {saveState.kind === 'saving' ? 'Saving…' : 'Save changes'}
        </Button>
        {dirty && saveState.kind !== 'saving' && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setDraft(draftFrom(entries))
              setSaveState({ kind: 'idle' })
            }}
          >
            Discard
          </Button>
        )}
        {saveState.kind === 'saved' && (
          <span className="text-xs text-success">Saved. Live now.</span>
        )}
        {saveState.kind === 'error' && (
          <span className="text-xs text-danger">{saveState.message}</span>
        )}
      </div>
    </Card>
  )
}
