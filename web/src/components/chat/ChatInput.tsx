import { useState, useRef, useEffect } from 'react'

interface Props {
  onSend: (content: string) => void
  onStop: () => void
  isStreaming: boolean
}

export function ChatInput({ onSend, onStop, isStreaming }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 150) + 'px'
    }
  }, [input])

  const handleSubmit = () => {
    if (!input.trim() || isStreaming) return
    onSend(input.trim())
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="px-6 py-4 border-t border-border bg-surface-secondary">
      <div className="flex items-end gap-3">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          rows={1}
          className="flex-1 bg-surface text-text-primary text-sm rounded-xl px-4 py-3 border border-border focus:border-accent focus:outline-none resize-none"
        />
        {isStreaming ? (
          <button
            onClick={onStop}
            className="px-4 py-3 rounded-xl bg-danger text-white font-medium text-sm hover:bg-danger/80 transition-colors cursor-pointer shrink-0"
          >
            Stop
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!input.trim()}
            className="px-4 py-3 rounded-xl bg-accent text-white font-medium text-sm hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
          >
            Send
          </button>
        )}
      </div>
    </div>
  )
}
