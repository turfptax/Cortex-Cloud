import { type ChatMessage } from '../../hooks/useChat'

interface Props {
  message: ChatMessage
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  if (isSystem) return null

  // Summary marker — collapsed, expandable
  if (message.isSummary) {
    return (
      <div className="flex justify-center my-2">
        <details className="w-full max-w-[85%]">
          <summary className="flex items-center justify-center gap-2 cursor-pointer text-xs text-text-muted hover:text-text-secondary py-2 px-4 rounded-lg bg-surface-tertiary/50 border border-border/50">
            <span className="text-success">&#9679;</span>
            Memory compacted — earlier messages summarized
          </summary>
          <div className="mt-2 px-4 py-3 rounded-lg bg-surface-tertiary/30 border border-border/30">
            <p className="text-xs text-text-muted leading-relaxed whitespace-pre-wrap">
              {message.content.replace('[Earlier conversation summarized: ', '').replace(/]$/, '')}
            </p>
          </div>
        </details>
      </div>
    )
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className="flex flex-col gap-1 max-w-[75%]">
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? 'bg-accent text-white rounded-br-md'
              : 'bg-surface-tertiary text-text-primary rounded-bl-md'
          }`}
        >
          <p className="text-sm whitespace-pre-wrap leading-relaxed">
            {message.content}
            {!isUser && !message.content && (
              <span className="inline-block w-2 h-4 bg-text-muted animate-pulse rounded-sm" />
            )}
          </p>
        </div>

      </div>
    </div>
  )
}
