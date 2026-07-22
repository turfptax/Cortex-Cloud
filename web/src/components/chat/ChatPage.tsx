import { useState, useEffect, useCallback, useRef } from 'react'
import { useChat } from '../../hooks/useChat'
import { MessageBubble } from './MessageBubble'
import { ChatInput } from './ChatInput'
import { apiFetch } from '../../lib/api'

interface ModelInfo {
  id: string
  object: string
  owned_by: string
}

export function ChatPage() {
  const {
    messages,
    isStreaming,
    isCompacting,
    systemPrompt,
    setSystemPrompt,
    resolvedPrompt,
    sendMessage,
    stopStreaming,
    clearMessages,
    memoryContext,
    tokenEstimate,
    allPresets,
    activePresetName,
    selectPreset,
    savePreset,
    deletePreset,
  } = useChat()

  const [showSystemPrompt, setShowSystemPrompt] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const [temperature, setTemperature] = useState(0.7)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [savePresetName, setSavePresetName] = useState('')
  const promptRef = useRef<HTMLTextAreaElement>(null)

  // Fetch available models from LM Studio on mount
  const fetchModels = useCallback(async () => {
    try {
      const data = await apiFetch<{ models: ModelInfo[] }>('/chat/models')
      setModels(data.models || [])
      if (!selectedModel && data.models?.length > 0) {
        setSelectedModel(data.models[0].id)
      }
    } catch (err) {
      console.error('Failed to fetch models:', err)
    }
  }, [selectedModel])

  useEffect(() => {
    fetchModels()
  }, [fetchModels])

  // Handle save dialog
  const handleSavePreset = () => {
    const name = savePresetName.trim()
    if (!name) return
    savePreset(name)
    setSaveDialogOpen(false)
    setSavePresetName('')
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border flex items-center justify-between bg-surface-secondary">
        <div>
          <h2 className="text-base font-semibold text-text-primary">
            Chat with LM Studio
          </h2>
          <div className="flex items-center gap-2">
            {/* Model selector dropdown */}
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="bg-surface-tertiary text-text-primary text-xs rounded-md px-2 py-1 border border-border focus:border-accent focus:outline-none cursor-pointer max-w-[220px]"
            >
              {models.length === 0 && (
                <option value="">Loading models...</option>
              )}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </select>

            {/* Refresh models button */}
            <button
              onClick={fetchModels}
              className="p-1 rounded text-text-muted hover:text-text-primary transition-colors cursor-pointer"
              title="Refresh model list"
            >
              <svg
                className="w-3 h-3"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
            </button>


            {tokenEstimate > 0 && (
              <span
                className={`text-xs px-1.5 py-0.5 rounded ${
                  tokenEstimate > 6000
                    ? 'bg-warning/20 text-warning'
                    : tokenEstimate > 4000
                      ? 'bg-accent/20 text-accent'
                      : 'bg-surface-tertiary text-text-muted'
                }`}
              >
                ~{tokenEstimate} tok
              </span>
            )}
            {memoryContext && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-success/20 text-success">
                memory active
              </span>
            )}
            {isCompacting && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-warning/20 text-warning animate-pulse">
                compacting...
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Temperature */}
          <div className="flex items-center gap-2">
            <label className="text-xs text-text-muted">Temp</label>
            <input
              type="range"
              min="0"
              max="2"
              step="0.1"
              value={temperature}
              onChange={(e) => setTemperature(parseFloat(e.target.value))}
              className="w-20 accent-accent"
            />
            <span className="text-xs text-text-secondary w-6">
              {temperature}
            </span>
          </div>

          <button
            onClick={() => setShowSystemPrompt(!showSystemPrompt)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer ${
              showSystemPrompt
                ? 'bg-accent/20 text-accent-hover'
                : 'bg-surface-tertiary text-text-secondary hover:text-text-primary'
            }`}
          >
            System
          </button>
          <button
            onClick={clearMessages}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary text-text-secondary hover:text-text-primary transition-colors cursor-pointer"
          >
            Clear
          </button>
        </div>
      </div>

      {/* ── System Prompt Editor Panel ── */}
      {showSystemPrompt && (
        <div className="px-6 py-3 border-b border-border bg-surface-tertiary/50 space-y-2">
          {/* Preset row */}
          <div className="flex items-center gap-2">
            <select
              value={activePresetName}
              onChange={(e) => selectPreset(e.target.value)}
              className="bg-surface text-text-primary text-xs rounded-md px-2 py-1 border border-border focus:border-accent focus:outline-none cursor-pointer"
            >
              {Object.keys(allPresets).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>

            <button
              onClick={() => {
                setSavePresetName('')
                setSaveDialogOpen(true)
              }}
              className="text-xs px-2 py-1 rounded bg-accent/20 text-accent hover:bg-accent/30 transition-colors cursor-pointer"
            >
              Save As
            </button>

            {/* Delete — only for user presets */}
            {activePresetName &&
              activePresetName !== 'Default' && (
                <button
                  onClick={() => deletePreset(activePresetName)}
                  className="text-xs px-2 py-1 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors cursor-pointer"
                >
                  Delete
                </button>
              )}

          </div>

          {/* Save dialog */}
          {saveDialogOpen && (
            <div className="flex items-center gap-2 bg-surface rounded-lg px-3 py-2 border border-border">
              <input
                type="text"
                value={savePresetName}
                onChange={(e) => setSavePresetName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSavePreset()}
                placeholder="Preset name..."
                autoFocus
                className="flex-1 bg-transparent text-sm text-text-primary focus:outline-none"
              />
              <button
                onClick={handleSavePreset}
                disabled={!savePresetName.trim()}
                className="text-xs px-2 py-1 rounded bg-accent text-white hover:bg-accent-hover transition-colors cursor-pointer disabled:opacity-40"
              >
                Save
              </button>
              <button
                onClick={() => setSaveDialogOpen(false)}
                className="text-xs px-2 py-1 rounded text-text-muted hover:text-text-primary cursor-pointer"
              >
                Cancel
              </button>
            </div>
          )}

          {/* Template textarea */}
          <textarea
            ref={promptRef}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="System prompt..."
            rows={4}
            className="w-full bg-surface text-text-primary text-sm rounded-lg p-3 border border-border focus:border-accent focus:outline-none resize-y font-mono"
          />

          {/* Live preview toggle */}
          <div>
            <button
              onClick={() => setShowPreview(!showPreview)}
              className="text-[10px] text-text-muted hover:text-text-secondary cursor-pointer flex items-center gap-1"
            >
              <span
                className={`inline-block transition-transform ${showPreview ? 'rotate-90' : ''}`}
              >
                &#9654;
              </span>
              Resolved preview
            </button>
            {showPreview && (
              <div className="mt-1 p-2 rounded bg-surface border border-border/50 text-xs text-text-muted whitespace-pre-wrap max-h-[100px] overflow-y-auto">
                {resolvedPrompt}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-text-muted">
            <div className="text-center">
              <p className="text-4xl mb-3">&#x1F4AC;</p>
              <p className="text-sm">
                Start a conversation with your local model
              </p>
              {selectedModel && (
                <p className="text-xs text-text-muted mt-1">
                  Model: {selectedModel}
                </p>
              )}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
      </div>

      {/* Input */}
      <ChatInput
        onSend={(content) =>
          sendMessage(content, selectedModel || undefined, temperature)
        }
        onStop={stopStreaming}
        isStreaming={isStreaming}
      />
    </div>
  )
}
