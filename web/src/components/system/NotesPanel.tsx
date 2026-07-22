import { useState } from 'react'

interface Props {
  notes: any[]
  onRefresh: (limit?: number) => void
  onSend: (
    content: string,
    tags?: string,
    project?: string,
    noteType?: string
  ) => Promise<boolean>
  isOnline: boolean
}

const noteTypes = ['note', 'decision', 'bug', 'reminder', 'idea', 'todo', 'context']

export function NotesPanel({ notes, onRefresh, onSend, isOnline }: Props) {
  const [content, setContent] = useState('')
  const [tags, setTags] = useState('')
  const [project, setProject] = useState('')
  const [noteType, setNoteType] = useState('note')
  const [sending, setSending] = useState(false)

  const handleSend = async () => {
    if (!content.trim()) return
    setSending(true)
    const ok = await onSend(content, tags, project, noteType)
    if (ok) {
      setContent('')
      setTags('')
      setProject('')
    }
    setSending(false)
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Send note form */}
      <div className="p-6 border-b border-border">
        <h3 className="text-sm font-semibold text-text-primary mb-3">
          Send Note
        </h3>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Write a note..."
          rows={3}
          disabled={!isOnline}
          className="w-full bg-surface text-text-primary text-sm rounded-lg p-3 border border-border focus:border-accent focus:outline-none resize-y disabled:opacity-50 mb-3"
        />
        <div className="flex items-end gap-3">
          <div className="flex-1 grid grid-cols-3 gap-2">
            <div>
              <label className="block text-xs text-text-muted mb-1">Type</label>
              <select
                value={noteType}
                onChange={(e) => setNoteType(e.target.value)}
                className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none"
              >
                {noteTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">Tags</label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="tag1,tag2"
                className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">
                Project
              </label>
              <input
                type="text"
                value={project}
                onChange={(e) => setProject(e.target.value)}
                placeholder="project name"
                className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none"
              />
            </div>
          </div>
          <button
            onClick={handleSend}
            disabled={!content.trim() || sending || !isOnline}
            className="px-4 py-2 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {sending ? 'Sending...' : 'Send'}
          </button>
        </div>
      </div>

      {/* Notes list */}
      <div className="p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-text-primary">
            Recent Notes ({notes.length})
          </h3>
          <button
            onClick={() => onRefresh()}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
          >
            Refresh
          </button>
        </div>
        {notes.length === 0 ? (
          <p className="text-sm text-text-muted text-center py-4">
            {isOnline ? 'No notes found' : 'Pi is offline'}
          </p>
        ) : (
          <div className="space-y-2">
            {notes.map((note: any, i: number) => (
              <div
                key={note.id || i}
                className="bg-surface-secondary rounded-lg p-3 border border-border"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm text-text-primary flex-1">
                    {note.content}
                  </p>
                  <span className="text-xs text-text-muted whitespace-nowrap">
                    {note.note_type || 'note'}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-2">
                  {note.project && (
                    <span className="px-2 py-0.5 rounded-full bg-accent/15 text-accent text-xs">
                      {note.project}
                    </span>
                  )}
                  {note.tags && (
                    <span className="text-xs text-text-muted">
                      {note.tags}
                    </span>
                  )}
                  {note.created_at && (
                    <span className="text-xs text-text-muted ml-auto">
                      {new Date(note.created_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
