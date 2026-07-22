import { useState, useEffect } from 'react'
import { apiFetch } from '../../lib/api'

interface McpInfo {
  mcp_config: Record<string, unknown>
  mcp_config_json: string
  python_path: string
  claude_config_path: string
  mcp_installed: boolean
  pip_install_cmd: string
}

export function McpSetupCard() {
  const [info, setInfo] = useState<McpInfo | null>(null)
  const [copied, setCopied] = useState(false)
  const [copiedPip, setCopiedPip] = useState(false)

  useEffect(() => {
    apiFetch<{ ok: boolean } & McpInfo>('/settings/mcp-config')
      .then((res) => setInfo(res))
      .catch(() => {})
  }, [])

  const handleCopy = async (text: string, setter: (v: boolean) => void) => {
    try {
      await navigator.clipboard.writeText(text)
      setter(true)
      setTimeout(() => setter(false), 2000)
    } catch {
      // fallback
    }
  }

  if (!info) {
    return (
      <div className="bg-surface rounded-xl border border-border p-6">
        <h2 className="text-lg font-semibold text-text-primary mb-4 flex items-center gap-2">
          <span>🤖</span> Claude MCP Setup
        </h2>
        <p className="text-sm text-text-muted">Loading...</p>
      </div>
    )
  }

  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <h2 className="text-lg font-semibold text-text-primary mb-4 flex items-center gap-2">
        <span>🤖</span> Claude MCP Setup
      </h2>

      <p className="text-sm text-text-secondary mb-4">
        Connect Cortex to Claude Desktop or Claude Code for AI-powered interaction with your Pi.
      </p>

      {/* Step 1: Install cortex-mcp */}
      <div className="mb-5">
        <h3 className="text-sm font-medium text-text-primary mb-2 flex items-center gap-2">
          <span className="w-5 h-5 rounded-full bg-accent/15 text-accent text-xs flex items-center justify-center font-bold">1</span>
          Install cortex-mcp
          {info.mcp_installed && (
            <span className="text-xs text-success ml-2">✅ Installed</span>
          )}
        </h3>
        {!info.mcp_installed && (
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 bg-surface-secondary border border-border rounded-lg text-xs text-text-primary font-mono">
              {info.pip_install_cmd}
            </code>
            <button
              onClick={() => handleCopy(info.pip_install_cmd, setCopiedPip)}
              className="px-3 py-2 bg-surface-tertiary text-text-primary text-xs rounded-lg hover:bg-surface-secondary transition-colors cursor-pointer"
            >
              {copiedPip ? '✅ Copied' : '📋 Copy'}
            </button>
          </div>
        )}
      </div>

      {/* Step 2: Add config */}
      <div className="mb-5">
        <h3 className="text-sm font-medium text-text-primary mb-2 flex items-center gap-2">
          <span className="w-5 h-5 rounded-full bg-accent/15 text-accent text-xs flex items-center justify-center font-bold">2</span>
          Add to Claude Desktop config
        </h3>
        <p className="text-xs text-text-muted mb-2">
          Add this to your config file at:
        </p>
        <code className="block px-3 py-2 bg-surface-secondary border border-border rounded-lg text-xs text-text-muted font-mono mb-3 break-all">
          {info.claude_config_path}
        </code>

        <div className="relative">
          <pre className="px-4 py-3 bg-surface-secondary border border-border rounded-lg text-xs text-text-primary font-mono overflow-x-auto whitespace-pre">
            {info.mcp_config_json}
          </pre>
          <button
            onClick={() => handleCopy(info.mcp_config_json, setCopied)}
            className="absolute top-2 right-2 px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-lg hover:bg-accent/15 hover:text-accent transition-colors cursor-pointer"
          >
            {copied ? '✅ Copied!' : '📋 Copy'}
          </button>
        </div>
      </div>

      {/* Step 3: Restart */}
      <div>
        <h3 className="text-sm font-medium text-text-primary mb-2 flex items-center gap-2">
          <span className="w-5 h-5 rounded-full bg-accent/15 text-accent text-xs flex items-center justify-center font-bold">3</span>
          Restart Claude Desktop
        </h3>
        <p className="text-xs text-text-muted">
          After saving the config, restart Claude Desktop. The Cortex tools will appear automatically.
        </p>
      </div>
    </div>
  )
}
