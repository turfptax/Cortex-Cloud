import { useState } from 'react'

interface Props {
  activeTable: string
  onSave: (table: string, data: Record<string, any>) => Promise<boolean>
  onRefresh: () => void
}

const inputClass = 'w-full bg-surface text-text-primary text-xs rounded-lg px-2.5 py-2 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 transition-all placeholder:text-text-muted/50'
const labelClass = 'block text-[10px] font-semibold text-text-muted mb-1 uppercase tracking-wider'
const selectClass = 'w-full bg-surface text-text-primary text-xs rounded-lg px-2.5 py-2 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 transition-all'

export function DataEntryForm({ activeTable, onSave, onRefresh }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [justSaved, setJustSaved] = useState(false)

  // Time entry fields
  const [teProject, setTeProject] = useState('')
  const [teOrg, setTeOrg] = useState('')
  const [teActivity, setTeActivity] = useState('coding')
  const [teDesc, setTeDesc] = useState('')
  const [teDuration, setTeDuration] = useState(30)

  // Note fields
  const [noteContent, setNoteContent] = useState('')
  const [noteType, setNoteType] = useState('note')
  const [noteProject, setNoteProject] = useState('')
  const [noteTags, setNoteTags] = useState('')

  // Project fields
  const [projTag, setProjTag] = useState('')
  const [projName, setProjName] = useState('')
  const [projCategory, setProjCategory] = useState('')
  const [projOrg, setProjOrg] = useState('')

  const handleSave = async () => {
    setSaving(true)
    let data: Record<string, any> = {}

    if (activeTable === 'time_entries') {
      data = {
        project_tag: teProject,
        org_tag: teOrg,
        activity_type: teActivity,
        description: teDesc,
        duration_minutes: teDuration,
        source: 'manual',
      }
    } else if (activeTable === 'notes') {
      data = {
        content: noteContent,
        note_type: noteType,
        project: noteProject,
        tags: noteTags,
        source: 'hub',
      }
    } else if (activeTable === 'projects') {
      data = {
        tag: projTag,
        name: projName,
        category: projCategory,
        org_tag: projOrg,
        status: 'active',
      }
    }

    const ok = await onSave(activeTable, data)
    setSaving(false)
    if (ok) {
      setJustSaved(true)
      setTimeout(() => setJustSaved(false), 1500)
      // Reset fields
      setTeDesc('')
      setNoteContent('')
      setProjTag('')
      setProjName('')
      onRefresh()
    }
  }

  if (!['time_entries', 'notes', 'projects'].includes(activeTable)) return null

  const formLabel = activeTable === 'time_entries' ? 'Time Entry'
    : activeTable === 'notes' ? 'Note'
    : 'Project'

  return (
    <div className="border-b border-border bg-surface">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2 text-left text-xs font-medium text-text-muted hover:text-text-primary transition-colors flex items-center gap-2 cursor-pointer group"
      >
        <span className={`text-[10px] transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}>
          ▶
        </span>
        <span>Quick Add {formLabel}</span>
        {justSaved && (
          <span className="ml-auto text-success text-[10px] font-semibold animate-pulse">
            ✓ Added!
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-3 pt-1">
          {activeTable === 'time_entries' && (
            <div className="grid grid-cols-6 gap-2.5 items-end">
              <div>
                <label className={labelClass}>Project</label>
                <input
                  type="text" value={teProject} onChange={e => setTeProject(e.target.value)}
                  placeholder="project-tag"
                  className={`${inputClass} font-mono`}
                />
              </div>
              <div>
                <label className={labelClass}>Org</label>
                <input
                  type="text" value={teOrg} onChange={e => setTeOrg(e.target.value)}
                  placeholder="org-tag"
                  className={`${inputClass} font-mono`}
                />
              </div>
              <div>
                <label className={labelClass}>Activity</label>
                <select
                  value={teActivity} onChange={e => setTeActivity(e.target.value)}
                  className={selectClass}
                >
                  {['coding', 'research', 'admin', 'meeting', 'design', 'infrastructure',
                    'development', 'ai-research', 'data-analysis', 'communication', 'media', 'writing'].map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelClass}>Description</label>
                <input
                  type="text" value={teDesc} onChange={e => setTeDesc(e.target.value)}
                  placeholder="What did you work on?"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Minutes</label>
                <input
                  type="number" value={teDuration} onChange={e => setTeDuration(parseInt(e.target.value) || 0)}
                  min={1}
                  className={`${inputClass} font-mono`}
                />
              </div>
              <button
                onClick={handleSave} disabled={saving || !teDesc}
                className="px-4 py-2 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-hover disabled:opacity-40 cursor-pointer transition-all shadow-sm shadow-accent/20"
              >
                {saving ? '...' : '+ Add'}
              </button>
            </div>
          )}

          {activeTable === 'notes' && (
            <div className="space-y-2.5">
              <textarea
                value={noteContent} onChange={e => setNoteContent(e.target.value)}
                placeholder="Write your note..."
                rows={2}
                className={`${inputClass} resize-y leading-relaxed`}
              />
              <div className="grid grid-cols-4 gap-2.5 items-end">
                <div>
                  <label className={labelClass}>Type</label>
                  <select
                    value={noteType} onChange={e => setNoteType(e.target.value)}
                    className={selectClass}
                  >
                    {['note', 'decision', 'bug', 'reminder', 'idea', 'todo', 'context'].map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelClass}>Project</label>
                  <input
                    type="text" value={noteProject} onChange={e => setNoteProject(e.target.value)}
                    placeholder="project-tag"
                    className={`${inputClass} font-mono`}
                  />
                </div>
                <div>
                  <label className={labelClass}>Tags</label>
                  <input
                    type="text" value={noteTags} onChange={e => setNoteTags(e.target.value)}
                    placeholder="tag1, tag2"
                    className={inputClass}
                  />
                </div>
                <button
                  onClick={handleSave} disabled={saving || !noteContent}
                  className="px-4 py-2 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-hover disabled:opacity-40 cursor-pointer transition-all shadow-sm shadow-accent/20"
                >
                  {saving ? '...' : '+ Add Note'}
                </button>
              </div>
            </div>
          )}

          {activeTable === 'projects' && (
            <div className="grid grid-cols-5 gap-2.5 items-end">
              <div>
                <label className={labelClass}>Tag (ID)</label>
                <input
                  type="text" value={projTag} onChange={e => setProjTag(e.target.value)}
                  placeholder="my-project"
                  className={`${inputClass} font-mono`}
                />
              </div>
              <div>
                <label className={labelClass}>Name</label>
                <input
                  type="text" value={projName} onChange={e => setProjName(e.target.value)}
                  placeholder="My Project"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Category</label>
                <input
                  type="text" value={projCategory} onChange={e => setProjCategory(e.target.value)}
                  placeholder="hardware"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Org</label>
                <input
                  type="text" value={projOrg} onChange={e => setProjOrg(e.target.value)}
                  placeholder="org-tag"
                  className={`${inputClass} font-mono`}
                />
              </div>
              <button
                onClick={handleSave} disabled={saving || !projTag}
                className="px-4 py-2 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-hover disabled:opacity-40 cursor-pointer transition-all shadow-sm shadow-accent/20"
              >
                {saving ? '...' : '+ Add Project'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
