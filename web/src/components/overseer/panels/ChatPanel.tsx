import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { type VoiceState } from '../../../hooks/useVoiceMode'
import {
  type ChatStoredAttachment,
  type ChatToolCallSummary,
  type ChatMessage,
  type ChatThread,
  type ChatPrompt,
  type PendingAttachment,
  CHAT_MAX_FILES,
  CHAT_MAX_FILE_BYTES,
  CHAT_ALLOWED_EXTS,
  classifyKind,
  formatBytes,
  fmtRelative,
} from '../shared'

// The Pi stores datetime('now') (UTC, no tz suffix). Normalize so
// fmtRelative doesn't parse it as local time. Handles both the
// space-separated and 'T'-separated forms; leaves strings that
// already carry a zone (Z or +hh:mm) untouched.
function utcIso(s?: string | null): string {
  if (!s) return ''
  const t = s.includes('T') ? s : s.replace(' ', 'T')
  return /(Z|[+-]\d{2}:?\d{2})$/.test(t) ? t : t + 'Z'
}

export function ChatPanel({
  messages,
  input,
  setInput,
  sending,
  onSend,
  onClear,
  onRefresh,
  pending,
  onAddFiles,
  onRemovePending,
  voiceState,
  voiceError,
  voiceLastHeard,
  onEnterVoice,
  onExitVoice,
  directMode,
  onToggleDirectMode,
  threads,
  activeThreadId,
  onNewThread,
  onSelectThread,
  onRenameThread,
  onDeleteThread,
  prompts,
  onSavePrompt,
  onDeletePrompt,
  onSendFeedback,
  onDiscussFeedback,
}: {
  messages: ChatMessage[]
  input: string
  setInput: (v: string) => void
  sending: boolean
  onSend: () => void
  onClear: () => void
  onRefresh: () => void
  pending: PendingAttachment[]
  onAddFiles: (files: FileList | File[]) => void
  onRemovePending: (localId: string) => void
  voiceState: VoiceState
  voiceError: string | null
  voiceLastHeard: string
  onEnterVoice: () => void
  onExitVoice: () => void
  directMode: boolean
  onToggleDirectMode: () => void
  threads: ChatThread[]
  activeThreadId: number
  onNewThread: () => void
  onSelectThread: (id: number) => void
  onRenameThread: (id: number, title: string) => void
  onDeleteThread: (id: number) => void
  prompts: ChatPrompt[]
  onSavePrompt: (title: string, body: string) => void
  onDeletePrompt: (id: number) => void
  onSendFeedback: (targetId: number, rating: number, note: string) => Promise<number | null>
  onDiscussFeedback: (feedbackId: number) => Promise<void>
}) {
  const voiceActive = voiceState !== 'off'
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesScrollRef = useRef<HTMLDivElement | null>(null)
  const [dragOver, setDragOver] = useState<boolean>(false)
  const [stickToBottom, setStickToBottom] = useState<boolean>(true)
  const [showPrompts, setShowPrompts] = useState<boolean>(false)
  const promptsPopoverRef = useRef<HTMLDivElement | null>(null)
  const activeThread = threads.find((t) => t.id === activeThreadId)

  // Close the prompt popover on outside click or Escape.
  useEffect(() => {
    if (!showPrompts) return
    const onDown = (e: MouseEvent) => {
      if (promptsPopoverRef.current &&
          !promptsPopoverRef.current.contains(e.target as Node)) {
        setShowPrompts(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowPrompts(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [showPrompts])

  // Auto-scroll-to-bottom when new messages arrive — but only if the
  // user is already at (or near) the bottom. Reading older messages
  // shouldn't yank them down on every fetch tick.
  useEffect(() => {
    if (!stickToBottom) return
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sending, stickToBottom])

  // Track scroll position so stickToBottom flips off when the user
  // scrolls up to read history, and back on when they return to the bottom.
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setStickToBottom(distanceFromBottom < 80)
  }

  const sendDisabled = sending
    || (!input.trim() && pending.filter((p) => !p.error).length === 0)
    || pending.some((p) => p.status === 'uploading')

  const droppableHandlers = {
    onDragEnter: (e: React.DragEvent) => {
      if (Array.from(e.dataTransfer.types).includes('Files')) {
        e.preventDefault(); setDragOver(true)
      }
    },
    onDragOver: (e: React.DragEvent) => {
      if (Array.from(e.dataTransfer.types).includes('Files')) {
        e.preventDefault(); e.dataTransfer.dropEffect = 'copy'
      }
    },
    onDragLeave: (e: React.DragEvent) => {
      // Only clear when leaving the wrapping element itself, not a child
      if (e.currentTarget === e.target) setDragOver(false)
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault(); setDragOver(false)
      const files = e.dataTransfer.files
      if (files && files.length) onAddFiles(files)
    },
  }

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Agent harness (2026-07-10): thread sidebar. Selecting a
          thread switches the Pi's active pointer, then reloads
          history — sends always target the active thread. */}
      <div className="w-56 shrink-0 border-r border-border flex flex-col bg-surface-secondary/40">
        <div className="p-2 border-b border-border">
          <button
            onClick={onNewThread}
            disabled={sending}
            className="w-full px-3 py-1.5 rounded-md text-xs font-medium bg-accent/15 hover:bg-accent/25 text-accent border border-accent/30 cursor-pointer disabled:opacity-50"
          >
            + New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {threads.length === 0 ? (
            <div className="px-3 py-4 text-[11px] text-text-muted">
              No threads yet — send a message to start one.
            </div>
          ) : (
            threads.map((t) => (
              <ThreadRow
                key={t.id}
                thread={t}
                active={t.id === activeThreadId}
                disabled={sending}
                onSelect={() => onSelectThread(t.id)}
                onRename={() => {
                  const title = window.prompt('Thread title:', t.title || '')
                  if (title !== null && title.trim()) {
                    onRenameThread(t.id, title.trim())
                  }
                }}
                onDelete={() => {
                  if (confirm(`Delete thread "${t.title || 'Untitled'}" and its ${t.message_count} message${t.message_count === 1 ? '' : 's'}? This cannot be undone.`)) {
                    onDeleteThread(t.id)
                  }
                }}
              />
            ))
          )}
        </div>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-6 py-2 border-b border-border">
        <div className="text-xs text-text-muted truncate">
          <span className="font-medium text-text-secondary">
            {activeThread?.title || 'Untitled thread'}
          </span>
          {' · '}
          {messages.length} message{messages.length === 1 ? '' : 's'}
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={onRefresh}
            disabled={sending}
            className="px-3 py-1 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            onClick={onClear}
            disabled={sending || messages.length === 0}
            className="px-3 py-1 rounded-md text-xs font-medium text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
          >
            Clear thread
          </button>
        </div>
      </div>

      <div
        ref={messagesScrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-4"
      >
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 ? (
            <div className="text-sm text-text-muted text-center py-12">
              Talk to the overseer. It has access to your working memory,
              recent gists, themes, and the institutional notes left by the
              first instance. Ask anything — what you've been working on,
              what you might be forgetting, what it thinks of a pattern it
              has noticed.
              <div className="mt-4 text-xs">
                Drop a file or click the paperclip to attach images, code,
                docs, or PDFs.
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <ChatBubble
                key={m.id}
                m={m}
                onSendFeedback={onSendFeedback}
                onDiscussFeedback={onDiscussFeedback}
              />
            ))
          )}
          {sending && (
            <div className="text-xs text-text-muted">
              <span className="inline-block animate-pulse">Thinking…</span>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div
        className={`border-t border-border px-6 py-3 bg-surface-secondary relative ${
          dragOver ? 'ring-2 ring-accent ring-inset' : ''
        }`}
        {...droppableHandlers}
      >
        {dragOver && (
          <div className="absolute inset-0 flex items-center justify-center bg-accent/10 pointer-events-none z-10">
            <div className="text-sm font-medium text-accent">
              Drop to attach (max {CHAT_MAX_FILES}, {formatBytes(CHAT_MAX_FILE_BYTES)} each)
            </div>
          </div>
        )}

        {pending.length > 0 && (
          <div className="max-w-3xl mx-auto mb-2 flex flex-wrap gap-2">
            {pending.map((p) => (
              <PendingAttachmentChip
                key={p.localId}
                p={p}
                onRemove={() => onRemovePending(p.localId)}
              />
            ))}
          </div>
        )}

        {/* Slice 14: voice mode status banner */}
        {voiceActive && (
          <div className="max-w-3xl mx-auto mb-2">
            <VoiceModeBanner
              state={voiceState}
              lastHeard={voiceLastHeard}
              onExit={onExitVoice}
            />
          </div>
        )}
        {voiceError && (
          <div className="max-w-3xl mx-auto mb-2 text-xs text-red-400">
            {voiceError}
          </div>
        )}

        <div className="max-w-3xl mx-auto flex gap-2 items-end">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={[...CHAT_ALLOWED_EXTS].join(',')}
            className="hidden"
            onChange={(e) => {
              if (e.target.files && e.target.files.length) {
                onAddFiles(e.target.files)
              }
              // Reset so the same filename can be picked again later
              e.target.value = ''
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={sending || voiceActive || pending.length >= CHAT_MAX_FILES}
            title="Attach files (max 10, 5MB each)"
            className="h-9 w-9 shrink-0 rounded-md flex items-center justify-center bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-muted hover:text-text-primary cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Attach files"
          >
            {/* Paperclip icon, simple inline SVG so we don't pull a deps */}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
            </svg>
          </button>
          {/* Slice 14: voice mode toggle — one press enters continuous
              voice conversation, press again to exit to text. */}
          <button
            type="button"
            onClick={() => (voiceActive ? onExitVoice() : onEnterVoice())}
            disabled={sending}
            title={voiceActive
              ? 'Exit voice mode'
              : 'Voice mode — talk to the overseer'}
            className={`h-9 w-9 shrink-0 rounded-md flex items-center justify-center cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
              voiceActive
                ? 'bg-accent text-white'
                : 'bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-muted hover:text-text-primary'
            }`}
            aria-label={voiceActive ? 'Exit voice mode' : 'Enter voice mode'}
          >
            {voiceActive ? (
              /* Stop square */
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="5" y="5" width="14" height="14" rx="2" />
              </svg>
            ) : (
              /* Microphone */
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                   strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="23"/>
                <line x1="8" y1="23" x2="16" y2="23"/>
              </svg>
            )}
          </button>
          {/* Slice 14.7 CP4: direct-overseer override. Default off →
              chat posts to /quick-chat (Flash router, escalates when
              needed). When on, posts go straight to /chat (full Opus).
              Persisted in localStorage. */}
          <button
            type="button"
            onClick={onToggleDirectMode}
            disabled={sending || voiceActive}
            title={directMode
              ? 'Direct mode ON — talking to overseer (Opus) directly'
              : 'Direct mode OFF — using router (cheaper). Click to switch.'}
            className={`h-9 px-2 shrink-0 rounded-md text-[11px] font-medium cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
              directMode
                ? 'bg-purple-600 text-white'
                : 'bg-surface-tertiary text-text-muted hover:text-text-primary'
            }`}
            aria-label={directMode ? 'Direct mode on' : 'Direct mode off'}
            aria-pressed={directMode}
          >
            {directMode ? 'Direct' : 'Router'}
          </button>
          {/* Agent harness: prompt library picker. Click a prompt to
              insert its body into the composer; save the current
              composer text as a new prompt. */}
          <div className="relative shrink-0" ref={promptsPopoverRef}>
            <button
              type="button"
              onClick={() => setShowPrompts((v) => !v)}
              disabled={sending || voiceActive}
              title="Prompt library — insert a saved prompt"
              className={`h-9 w-9 rounded-md flex items-center justify-center cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
                showPrompts
                  ? 'bg-accent text-white'
                  : 'bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-muted hover:text-text-primary'
              }`}
              aria-label="Prompt library"
              aria-expanded={showPrompts}
            >
              {/* Book icon */}
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                   strokeLinejoin="round">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
              </svg>
            </button>
            {showPrompts && (
              <div className="absolute bottom-11 left-0 w-80 max-h-72 overflow-y-auto rounded-md border border-border bg-surface-secondary shadow-lg z-20">
                <div className="px-3 py-2 border-b border-border text-[10px] uppercase tracking-wide text-text-muted">
                  Prompt library
                </div>
                {prompts.length === 0 ? (
                  <div className="px-3 py-3 text-xs text-text-muted">
                    No saved prompts yet. Type something below and save it.
                  </div>
                ) : (
                  prompts.map((p) => (
                    <div
                      key={p.id}
                      className="group flex items-start gap-2 px-3 py-2 hover:bg-surface-tertiary/60 cursor-pointer border-b border-border/40 last:border-b-0"
                      onClick={() => {
                        setInput(input.trim() ? input + '\n\n' + p.body : p.body)
                        setShowPrompts(false)
                      }}
                      title="Insert into composer"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium text-text-primary truncate">
                          {p.title}
                        </div>
                        <div className="text-[11px] text-text-muted truncate">
                          {p.body.split('\n')[0]}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          if (confirm(`Delete prompt "${p.title}"?`)) {
                            onDeletePrompt(p.id)
                          }
                        }}
                        className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400 cursor-pointer text-xs shrink-0"
                        aria-label={`Delete prompt ${p.title}`}
                        title="Delete prompt"
                      >
                        ×
                      </button>
                    </div>
                  ))
                )}
                <div className="p-2 border-t border-border">
                  <button
                    type="button"
                    disabled={!input.trim()}
                    onClick={() => {
                      const title = window.prompt('Save composer text as prompt — title:')
                      if (title && title.trim()) {
                        onSavePrompt(title.trim(), input.trim())
                        setShowPrompts(false)
                      }
                    }}
                    className="w-full px-2 py-1.5 rounded text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-40"
                    title={input.trim() ? 'Save the current composer text as a reusable prompt' : 'Type something in the composer first'}
                  >
                    + Save composer text as prompt
                  </button>
                </div>
              </div>
            )}
          </div>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onSend()
              }
            }}
            placeholder={
              voiceActive
                ? 'Voice mode active — speak, or press the stop button to return to text.'
                : pending.length
                ? 'Add a question, or send the files alone…'
                : 'Type a message… (/ for commands, Enter to send, Shift+Enter for newline)'
            }
            disabled={sending || voiceActive}
            rows={2}
            className="flex-1 rounded-md border border-border bg-surface-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-accent disabled:opacity-50"
          />
          <button
            onClick={onSend}
            disabled={sendDisabled}
            className="px-4 py-2 rounded-md text-sm font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50 self-end"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
      </div>
    </div>
  )
}

// Agent harness (2026-07-10): one row in the thread sidebar. Active
// thread gets the accent treatment; rename/delete reveal on hover.
function ThreadRow({
  thread,
  active,
  disabled,
  onSelect,
  onRename,
  onDelete,
}: {
  thread: ChatThread
  active: boolean
  disabled: boolean
  onSelect: () => void
  onRename: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={`group flex items-start gap-1 px-3 py-2 border-b border-border/40 cursor-pointer ${
        active
          ? 'bg-accent/10 border-l-2 border-l-accent'
          : 'border-l-2 border-l-transparent hover:bg-surface-tertiary/50'
      } ${disabled ? 'opacity-60 pointer-events-none' : ''}`}
      onClick={onSelect}
      title={thread.title || 'Untitled'}
    >
      <div className="min-w-0 flex-1">
        <div className={`text-xs truncate ${
          active ? 'font-semibold text-text-primary' : 'font-medium text-text-secondary'
        }`}>
          {thread.title || 'Untitled'}
        </div>
        <div className="text-[10px] text-text-muted">
          {thread.message_count} msg{thread.message_count === 1 ? '' : 's'}
          {' · '}
          {fmtRelative(utcIso(thread.updated_at))}
        </div>
      </div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 shrink-0">
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onRename() }}
          className="text-text-muted hover:text-text-primary cursor-pointer text-[11px]"
          aria-label="Rename thread"
          title="Rename"
        >
          ✎
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          className="text-text-muted hover:text-red-400 cursor-pointer text-[11px]"
          aria-label="Delete thread"
          title="Delete"
        >
          ×
        </button>
      </div>
    </div>
  )
}

// Slice 14: voice-mode status banner. Shows the live state of the
// conversation loop (listening / transcribing / thinking / speaking)
// plus what was last heard, with a stop control.
export function VoiceModeBanner({
  state,
  lastHeard,
  onExit,
}: {
  state: VoiceState
  lastHeard: string
  onExit: () => void
}) {
  const label: Record<VoiceState, string> = {
    off: '',
    listening: 'Listening…',
    transcribing: 'Transcribing…',
    thinking: 'Overseer is thinking…',
    speaking: 'Speaking…',
  }
  const dotColor: Record<VoiceState, string> = {
    off: '#64748b',
    listening: '#10b981',
    transcribing: '#06b6d4',
    thinking: '#f59e0b',
    speaking: '#7c5cff',
  }
  return (
    <div className="flex items-center gap-3 rounded-md bg-surface-tertiary px-3 py-2">
      <span
        className="w-2.5 h-2.5 rounded-full shrink-0"
        style={{
          background: dotColor[state],
          animation: state === 'listening' || state === 'speaking'
            ? 'pulse 1.4s ease-in-out infinite'
            : undefined,
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-text-primary">
          Voice mode · {label[state]}
        </div>
        {lastHeard && (
          <div className="text-[11px] text-text-muted truncate">
            heard: "{lastHeard}"
          </div>
        )}
      </div>
      <button
        onClick={onExit}
        className="text-xs px-2 py-1 rounded bg-surface-secondary hover:bg-red-500/20 text-text-secondary hover:text-red-400 cursor-pointer shrink-0"
      >
        Stop
      </button>
    </div>
  )
}

// Slice 9.6 CP1 (2026-05-19): renders overseer-attached custom action
// buttons on a notification. Four interaction models depending on
// action.kind:
//   - 'free_text'         → click expands a textarea; submit logs reply
//   - 'yes_no'            → two button cluster: Yes / No (auto-payload)
//   - 'dispatch_sibling'  → single button (overseer reads + creates the
//                            sibling task on next tick from the response
//                            payload)
//   - all other kinds     → click immediately POSTs the action's payload
//                            verbatim ('archive_project', 'mark_dormant',
//                            etc. — overseer reads the response next tick
//                            and acts via its write tools)

export function PendingAttachmentChip({
  p, onRemove,
}: { p: PendingAttachment; onRemove: () => void }) {
  const kind = classifyKind(p.filename, p.mime_type)
  const isError = p.status === 'error' || !!p.error
  const isUploading = p.status === 'uploading'

  return (
    <div
      className={`flex items-center gap-2 max-w-xs rounded-md border px-2 py-1 text-xs ${
        isError
          ? 'border-red-500/40 bg-red-500/10 text-red-300'
          : 'border-border bg-surface-tertiary text-text-primary'
      }`}
      title={isError ? p.error : `${p.filename} · ${formatBytes(p.size)}`}
    >
      {kind === 'image' && p.previewUrl ? (
        <img src={p.previewUrl} alt={p.filename}
             className="h-8 w-8 object-cover rounded" />
      ) : (
        <span className="h-8 w-8 shrink-0 rounded flex items-center justify-center text-[10px] uppercase tracking-wide bg-surface-secondary text-text-muted">
          {kind === 'pdf' ? 'PDF' : kind === 'text' ? 'TXT' : 'FILE'}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{p.filename}</div>
        <div className="text-[10px] text-text-muted">
          {isUploading
            ? 'uploading…'
            : isError
              ? (p.error || 'error')
              : p.status === 'ready'
                ? `${formatBytes(p.size)} · ready`
                : formatBytes(p.size)}
        </div>
      </div>
      <button
        onClick={onRemove}
        disabled={isUploading}
        className="text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
        aria-label="Remove attachment"
        title="Remove"
      >
        ×
      </button>
    </div>
  )
}

export function ChatBubble({
  m,
  onSendFeedback,
  onDiscussFeedback,
}: {
  m: ChatMessage
  onSendFeedback?: (targetId: number, rating: number, note: string) => Promise<number | null>
  onDiscussFeedback?: (feedbackId: number) => Promise<void>
}) {
  const isUser = m.role === 'user'
  const isSystem = m.role === 'system'
  const attachments = m.attachments || []
  return (
    <div
      className={`flex ${
        isUser ? 'justify-end' : isSystem ? 'justify-center' : 'justify-start'
      }`}
    >
      <div
        className={`max-w-[80%] rounded-lg px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? 'bg-accent/15 text-text-primary border border-accent/30'
            : isSystem
              ? 'bg-surface-tertiary/40 text-text-secondary border border-border/40 max-w-[90%]'
              : 'bg-surface-secondary text-text-primary border border-border'
        }`}
      >
        <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1 flex items-center gap-2 flex-wrap">
          <span>
            {isUser
              ? 'you'
              : isSystem
                ? 'system'
                : m.answered_by === 'router'
                  ? 'router'
                  : 'overseer'}
          </span>
          {/* Slice 14.7 CP4: layer badge — emerald for router, purple
              for overseer. Only on assistant rows that carry the
              attribution tag (pre-14.7 rows have no answered_by). */}
          {!isUser && !isSystem && m.answered_by && (
            <span
              className={`normal-case px-1.5 py-0.5 rounded text-[9px] font-semibold ${
                m.answered_by === 'router'
                  ? 'bg-emerald-500/20 text-emerald-300'
                  : 'bg-purple-500/20 text-purple-300'
              }`}
              title={m.escalation_reason
                ? `Escalated: ${m.escalation_reason}`
                : (m.answered_by === 'router'
                    ? 'Answered by the cheap Flash router'
                    : 'Answered by the full Opus overseer')}
            >
              {m.answered_by === 'router' ? 'Router' : 'Overseer'}
              {m.escalation_reason && ' ↑'}
            </span>
          )}
          {!isUser && !isSystem && m.model && (
            <span className="normal-case">
              {m.model} · {m.latency_ms}ms · ${(m.cost_usd ?? 0).toFixed(4)}
            </span>
          )}
        </div>

        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map((a) => (
              <ChatAttachmentBadge key={a.id} a={a} />
            ))}
          </div>
        )}

        {/* Slice 9.5 CP1 (2026-05-19): assistant messages render as
            markdown (GFM = tables, strikethrough, task lists). User
            messages stay plain to avoid surprising formatting if Tory
            types backtick code or # headers conversationally. */}
        {isUser ? (
          <div className="whitespace-pre-wrap">{m.content}</div>
        ) : (
          <div className="chat-markdown text-text-primary">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p: ({ children }) => (
                  <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>
                ),
                ul: ({ children }) => (
                  <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>
                ),
                ol: ({ children }) => (
                  <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>
                ),
                li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                h1: ({ children }) => (
                  <h1 className="text-base font-semibold mb-2 mt-3 first:mt-0">{children}</h1>
                ),
                h2: ({ children }) => (
                  <h2 className="text-sm font-semibold mb-2 mt-3 first:mt-0">{children}</h2>
                ),
                h3: ({ children }) => (
                  <h3 className="text-sm font-medium mb-1 mt-2 first:mt-0">{children}</h3>
                ),
                code: ({ className, children, ...rest }) => {
                  const isInline = !className
                  return isInline ? (
                    <code className="rounded bg-surface-tertiary px-1 py-0.5 text-xs font-mono">
                      {children}
                    </code>
                  ) : (
                    <code className={className} {...rest}>
                      {children}
                    </code>
                  )
                },
                pre: ({ children }) => (
                  <pre className="rounded bg-surface-tertiary border border-border px-3 py-2 my-2 text-xs font-mono overflow-x-auto">
                    {children}
                  </pre>
                ),
                blockquote: ({ children }) => (
                  <blockquote className="border-l-2 border-accent/40 pl-3 my-2 text-text-secondary italic">
                    {children}
                  </blockquote>
                ),
                a: ({ href, children }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent hover:underline"
                  >
                    {children}
                  </a>
                ),
                strong: ({ children }) => (
                  <strong className="font-semibold text-text-primary">{children}</strong>
                ),
                table: ({ children }) => (
                  <div className="overflow-x-auto my-2">
                    <table className="text-xs border-collapse">{children}</table>
                  </div>
                ),
                th: ({ children }) => (
                  <th className="border border-border px-2 py-1 bg-surface-tertiary font-medium">
                    {children}
                  </th>
                ),
                td: ({ children }) => (
                  <td className="border border-border px-2 py-1">{children}</td>
                ),
              }}
            >
              {m.content}
            </ReactMarkdown>
          </div>
        )}

        {/* Slice 9.5 CP1: tool-call audit. Shows which tools overseer
            invoked and in what order. Multi-iter loops collapse by
            iter number so a 3-iteration tool-use exchange reads
            cleanly. Tory's directive: "We will want to show the tool
            use of overseer as well in the chat screen." */}
        {!isUser && !isSystem && m.tool_calls && m.tool_calls.length > 0 && (
          <ChatToolCallList calls={m.tool_calls} iterations={m.tool_iterations} />
        )}

        {/* Agent harness (2026-07-11): meta-feedback on real assistant
            turns (m.id > 0 excludes optimistic stubs + slash bubbles).
            Note-first; Discuss with Overseer is the secondary path. */}
        {!isUser && !isSystem && m.id > 0 && onSendFeedback && (
          <FeedbackControl
            targetId={m.id}
            onSend={onSendFeedback}
            onDiscuss={onDiscussFeedback}
          />
        )}
      </div>
    </div>
  )
}

// Agent harness (2026-07-11): lightweight per-turn feedback. Two tiny
// thumbs; picking one opens an inline note box. Save is the default
// action; "Discuss with Overseer" escalates to a context-seeded
// thread (it saves first if needed so the thread links the feedback).
function FeedbackControl({
  targetId,
  onSend,
  onDiscuss,
}: {
  targetId: number
  onSend: (targetId: number, rating: number, note: string) => Promise<number | null>
  onDiscuss?: (feedbackId: number) => Promise<void>
}) {
  const [rating, setRating] = useState<number>(0)
  const [open, setOpen] = useState<boolean>(false)
  const [note, setNote] = useState<string>('')
  const [busy, setBusy] = useState<boolean>(false)
  const [savedId, setSavedId] = useState<number | null>(null)
  const [savedRating, setSavedRating] = useState<number>(0)

  const pick = (r: number) => {
    if (busy) return
    setRating(r)
    setOpen(true)
  }

  const save = async (): Promise<number | null> => {
    setBusy(true)
    try {
      const id = await onSend(targetId, rating, note.trim())
      if (id) {
        setSavedId(id)
        setSavedRating(rating)
        setOpen(false)
      }
      return id
    } finally {
      setBusy(false)
    }
  }

  const discuss = async () => {
    if (!onDiscuss) return
    setBusy(true)
    try {
      const id = savedId ?? (await onSend(targetId, rating, note.trim()))
      if (id) {
        setSavedId(id)
        setSavedRating(rating)
        setOpen(false)
        await onDiscuss(id)
      }
    } finally {
      setBusy(false)
    }
  }

  if (savedId && !open) {
    return (
      <div className="mt-2 flex items-center gap-2 text-[10px] text-text-muted">
        <span>{savedRating > 0 ? '👍' : savedRating < 0 ? '👎' : '✎'} feedback noted</span>
        {onDiscuss && (
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              if (busy) return
              setBusy(true)
              try { await onDiscuss(savedId) } finally { setBusy(false) }
            }}
            className="text-accent hover:underline cursor-pointer disabled:opacity-50"
            title="Open the discussion thread for this feedback"
          >
            {busy ? 'opening…' : 'discuss'}
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="mt-2">
      <div className="flex items-center gap-1 opacity-60 hover:opacity-100">
        <button
          type="button"
          onClick={() => pick(1)}
          disabled={busy}
          aria-label="Good response"
          title="Good response"
          className={`h-6 w-6 rounded text-[11px] flex items-center justify-center cursor-pointer disabled:opacity-50 ${
            rating > 0 && open ? 'bg-emerald-500/20' : 'hover:bg-surface-tertiary'
          }`}
        >
          👍
        </button>
        <button
          type="button"
          onClick={() => pick(-1)}
          disabled={busy}
          aria-label="Bad response"
          title="Bad response"
          className={`h-6 w-6 rounded text-[11px] flex items-center justify-center cursor-pointer disabled:opacity-50 ${
            rating < 0 && open ? 'bg-red-500/20' : 'hover:bg-surface-tertiary'
          }`}
        >
          👎
        </button>
      </div>
      {open && (
        <div className="mt-1 rounded-md border border-border bg-surface-tertiary/40 p-2 space-y-2">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note — what worked or didn't?"
            rows={2}
            className="w-full rounded border border-border bg-surface-tertiary px-2 py-1 text-xs text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-accent"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={busy}
              className="px-2.5 py-1 rounded text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Save'}
            </button>
            {onDiscuss && (
              <button
                type="button"
                onClick={discuss}
                disabled={busy}
                className="px-2.5 py-1 rounded text-xs text-text-muted hover:text-text-primary cursor-pointer disabled:opacity-50"
                title="Save this feedback and open a thread with the overseer, seeded with this exchange"
              >
                Discuss with Overseer
              </button>
            )}
            <button
              type="button"
              onClick={() => { setOpen(false); setRating(0) }}
              disabled={busy}
              className="ml-auto text-xs text-text-muted hover:text-text-primary cursor-pointer"
            >
              cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// Slice 9.5 CP1: tool-call audit display under assistant messages.
// Compact by default (one row per call); each row expands on click
// to show the args + result size. Keeps the chat readable while
// preserving the audit trail.
export function ChatToolCallList({
  calls,
  iterations,
}: {
  calls: ChatToolCallSummary[]
  iterations?: number
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  if (!calls.length) return null
  const toggle = (i: number) => {
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }
  return (
    <div className="mt-3 pt-2 border-t border-border/40">
      <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
        tool calls ({calls.length}
        {iterations && iterations !== calls.length ? ` across ${iterations} iter` : ''})
      </div>
      <div className="space-y-1">
        {calls.map((c, i) => {
          const isOpen = expanded.has(i)
          return (
            <div
              key={i}
              className="rounded bg-surface-tertiary/50 border border-border/40 text-xs"
            >
              <button
                type="button"
                onClick={() => toggle(i)}
                className="w-full px-2 py-1 flex items-center gap-2 text-left hover:bg-surface-tertiary/80 cursor-pointer"
              >
                <span className="text-[10px] text-text-muted font-mono">#{c.iter}</span>
                <span className="font-mono text-accent flex-1 truncate">{c.name}</span>
                <span className="text-[10px] text-text-muted whitespace-nowrap">
                  {c.result_chars >= 1024
                    ? `${(c.result_chars / 1024).toFixed(1)}k`
                    : c.result_chars}{' '}
                  chars
                </span>
                <span className="text-text-muted text-[10px]">{isOpen ? '▾' : '▸'}</span>
              </button>
              {isOpen && (
                <div className="px-2 pb-2 pt-1 border-t border-border/40">
                  <div className="text-[10px] text-text-muted mb-0.5">args:</div>
                  <pre className="text-[10px] font-mono whitespace-pre-wrap break-all text-text-secondary">
                    {JSON.stringify(c.args, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function ChatAttachmentBadge({ a }: { a: ChatStoredAttachment }) {
  // Slice 8: render an image thumbnail when possible, otherwise a
  // labelled chip. The thumbnail uses /api/pi/files/<category>/<name>
  // when the file lives under uploads/, falling back to a generic
  // chip if that route isn't available. For now we punt on the image
  // src and render a text chip everywhere — the chat history still
  // shows what was attached. Slice D will add real thumbs.
  const label = a.kind === 'image' ? 'IMG'
    : a.kind === 'pdf' ? 'PDF'
    : a.kind === 'text' ? 'TXT' : 'FILE'
  return (
    <div
      className="flex items-center gap-2 max-w-xs rounded-md border border-border bg-surface-tertiary/50 px-2 py-1 text-xs"
      title={`${a.filename} · ${formatBytes(a.size_bytes)} · ${a.mime_type || a.kind}`}
    >
      <span className="h-7 w-7 shrink-0 rounded flex items-center justify-center text-[10px] uppercase tracking-wide bg-surface-secondary text-text-muted">
        {label}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{a.filename}</div>
        <div className="text-[10px] text-text-muted">
          {formatBytes(a.size_bytes)}
        </div>
      </div>
    </div>
  )
}

