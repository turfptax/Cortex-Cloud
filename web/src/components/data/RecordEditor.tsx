import { useState, useEffect, useMemo } from 'react'

interface Props {
  record: Record<string, any>
  table: string
  onSave: (table: string, data: Record<string, any>) => Promise<boolean>
  onDelete: (table: string, id: string | number) => Promise<boolean>
  onClose: () => void
  onRefresh: () => void
}

// Enum options for known fields
const ENUM_OPTIONS: Record<string, string[]> = {
  note_type: ['note', 'decision', 'bug', 'reminder', 'idea', 'todo', 'context'],
  status: ['active', 'archived', 'paused', 'completed'],
  org_type: ['employer', 'contractor', 'nonprofit', 'consulting', 'startup', 'personal'],
  activity_type: ['coding', 'research', 'design', 'writing', 'meeting', 'admin',
    'infrastructure', 'development', 'data-analysis', 'ai-research', 'ai-development',
    'communication', 'media', 'migration', 'video', 'fabrication', 'testing',
    'debugging', 'reviewing', 'learning', 'brainstorming'],
}

// Fields that should be textarea
const TEXTAREA_FIELDS = new Set(['content', 'description', 'notes', 'summary'])

// Read-only fields
const READONLY_FIELDS = new Set(['id', 'created_at', 'last_touched'])

// Field grouping for visual organization
const META_FIELDS = new Set(['id', 'created_at', 'last_touched', 'source', 'session_id'])

function getPrimaryKey(table: string): string {
  if (table === 'projects' || table === 'organizations') return 'tag'
  if (table === 'computers') return 'hostname'
  if (table === 'people') return 'id'
  return 'id'
}

function fieldLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
    .replace('Url', 'URL')
    .replace('Id', 'ID')
}

export function RecordEditor({ record, table, onSave, onDelete, onClose, onRefresh }: Props) {
  const [local, setLocal] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const isNew = useMemo(() => {
    const pk = getPrimaryKey(table)
    return !record[pk]
  }, [record, table])

  useEffect(() => {
    setLocal({ ...record })
    setSaved(false)
    setConfirmDelete(false)
  }, [record])

  // Split fields into main and meta groups
  const { mainFields, metaFields } = useMemo(() => {
    const all = Object.keys(local).filter(k => k !== 'session_id')
    return {
      mainFields: all.filter(k => !META_FIELDS.has(k)),
      metaFields: all.filter(k => META_FIELDS.has(k)),
    }
  }, [local])

  const setValue = (key: string, value: any) => {
    setLocal(prev => ({ ...prev, [key]: value }))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    const ok = await onSave(table, local)
    setSaving(false)
    if (ok) {
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      onRefresh()
    }
  }

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setDeleting(true)
    const pk = getPrimaryKey(table)
    const id = local[pk]
    if (id != null) {
      const ok = await onDelete(table, id)
      if (ok) {
        onClose()
        onRefresh()
      }
    }
    setDeleting(false)
  }

  const renderField = (key: string) => {
    const isReadonly = READONLY_FIELDS.has(key)
    const isEnum = ENUM_OPTIONS[key]
    const isTextarea = TEXTAREA_FIELDS.has(key)
    const value = local[key] ?? ''

    return (
      <div key={key}>
        <label className="block text-[11px] font-medium text-text-muted mb-1.5 uppercase tracking-wide">
          {fieldLabel(key)}
        </label>
        {isReadonly ? (
          <div className="text-xs text-text-secondary font-mono bg-surface rounded-lg px-3 py-2 border border-border/50">
            {String(value) || '—'}
          </div>
        ) : isEnum ? (
          <select
            value={String(value)}
            onChange={e => setValue(key, e.target.value)}
            className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 transition-all"
          >
            <option value="">—</option>
            {isEnum.map(opt => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : isTextarea ? (
          <textarea
            value={String(value)}
            onChange={e => setValue(key, e.target.value)}
            rows={4}
            className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 resize-y transition-all leading-relaxed"
          />
        ) : typeof value === 'number' ? (
          <input
            type="number"
            value={value}
            onChange={e => setValue(key, parseFloat(e.target.value) || 0)}
            step={key.includes('hours') ? 0.1 : 1}
            className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 font-mono transition-all"
          />
        ) : (
          <input
            type="text"
            value={String(value)}
            onChange={e => setValue(key, e.target.value)}
            className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 transition-all"
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-surface-secondary">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex items-center justify-between bg-surface-secondary">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${isNew ? 'bg-accent' : 'bg-success'}`} />
          <h3 className="text-sm font-semibold text-text-primary">
            {isNew ? 'New Record' : 'Edit Record'}
          </h3>
        </div>
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-md text-text-muted hover:text-text-primary hover:bg-surface-tertiary text-sm leading-none cursor-pointer transition-colors"
          title="Close editor"
        >
          ✕
        </button>
      </div>

      {/* Fields */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Main editable fields */}
        <div className="space-y-3">
          {mainFields.map(renderField)}
        </div>

        {/* Meta fields in collapsed details */}
        {metaFields.length > 0 && (
          <details className="rounded-lg border border-border/50 bg-surface/30">
            <summary className="px-3 py-2 text-[11px] font-medium text-text-muted uppercase tracking-wide cursor-pointer hover:text-text-secondary transition-colors select-none">
              Metadata
            </summary>
            <div className="px-3 pb-3 space-y-3">
              {metaFields.map(renderField)}
            </div>
          </details>
        )}
      </div>

      {/* Actions */}
      <div className="px-4 py-3 border-t border-border bg-surface-secondary">
        <div className="flex items-center gap-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className={`flex-1 px-4 py-2.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
              saved
                ? 'bg-success/20 text-success border border-success/30'
                : 'bg-accent text-white hover:bg-accent-hover shadow-sm shadow-accent/20'
            } disabled:opacity-50`}
          >
            {saving ? (
              <span className="inline-flex items-center gap-2">
                <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Saving...
              </span>
            ) : saved ? (
              '✓ Saved!'
            ) : (
              'Save Changes'
            )}
          </button>
          {!isNew && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              className={`px-4 py-2.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                confirmDelete
                  ? 'bg-danger text-white shadow-sm shadow-danger/20'
                  : 'bg-surface text-danger border border-danger/20 hover:bg-danger/10'
              } disabled:opacity-50`}
            >
              {deleting ? '...' : confirmDelete ? 'Confirm' : 'Delete'}
            </button>
          )}
        </div>
        {confirmDelete && (
          <p className="mt-2 text-[11px] text-danger/80 text-center">
            Click Confirm to permanently delete this record
          </p>
        )}
      </div>
    </div>
  )
}
