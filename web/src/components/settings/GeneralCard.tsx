interface Props {
  config: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
}

export function GeneralCard({ config, onChange }: Props) {
  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <h2 className="text-lg font-semibold text-text-primary mb-4 flex items-center gap-2">
        <span>⚙️</span> General
      </h2>

      <div className="space-y-4">
        {/* LM Studio URL */}
        <div>
          <label className="block text-xs text-text-muted mb-1">LM Studio URL</label>
          <input
            type="text"
            value={(config.lmstudio_url as string) || ''}
            onChange={(e) => onChange('lmstudio_url', e.target.value)}
            placeholder="http://10.0.0.102:1234/v1"
            className="w-full px-3 py-2 bg-surface-secondary border border-border rounded-lg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
          />
          <p className="text-xs text-text-muted mt-1">
            Used for the Chat page. Leave empty if not using LM Studio.
          </p>
        </div>

        {/* Hub Port */}
        <div>
          <label className="block text-xs text-text-muted mb-1">Hub Port</label>
          <input
            type="number"
            value={(config.hub_port as number) || 8003}
            onChange={(e) => onChange('hub_port', parseInt(e.target.value) || 8003)}
            className="w-full px-3 py-2 bg-surface-secondary border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-accent max-w-[200px]"
          />
          <p className="text-xs text-text-muted mt-1">
            Requires restart to take effect.
          </p>
        </div>

        {/* Toggle: Auto-open browser */}
        <label className="flex items-center gap-3 cursor-pointer">
          <div
            onClick={() => onChange('auto_open_browser', !config.auto_open_browser)}
            className={`w-10 h-5 rounded-full transition-colors relative cursor-pointer ${
              config.auto_open_browser ? 'bg-accent' : 'bg-surface-tertiary'
            }`}
          >
            <div
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                config.auto_open_browser ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </div>
          <div>
            <span className="text-sm text-text-primary">Auto-open browser on launch</span>
            <p className="text-xs text-text-muted">
              Open the Hub in your browser when Cortex Desktop starts.
            </p>
          </div>
        </label>
      </div>
    </div>
  )
}
