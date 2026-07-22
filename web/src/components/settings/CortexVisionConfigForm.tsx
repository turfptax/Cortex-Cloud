import { useEffect, useState } from 'react'
import {
  getVisionConfig,
  updateVisionConfig,
  testVisionConfig,
  type VisionConfig,
  type VisionConfigSection,
  type VisionTestResponse,
  type VisionTestSection,
} from '../../lib/videoApi'

/** Configure form for cortex-vision's describer + transcribe providers.
 *
 * Loads current config from GET /api/video/config (api_keys redacted as
 * "***"), lets the user edit URL / model / api_key per section, runs
 * "Test connection" (POST /test) to populate the model dropdown from
 * the provider's actual model list, and saves via PUT /config (atomic,
 * no sidecar restart needed — config is read on each request).
 *
 * The "***" sentinel is the contract for keeping an existing api_key:
 *   - submit "***" -> server keeps current
 *   - submit ""    -> server clears
 *   - submit other -> server stores
 */

type SectionKey = 'describer' | 'transcribe'

const SECTION_LABELS: Record<SectionKey, { title: string; subtitle: string }> = {
  describer: {
    title: 'Describer',
    subtitle:
      'Vision-language model used to describe each scene’s keyframe. Must accept image input.',
  },
  transcribe: {
    title: 'Transcribe',
    subtitle:
      'Audio transcription provider used when the user opts in via the Transcribe audio toggle. Whisper-style HTTP API.',
  },
}

export function CortexVisionConfigForm({ onClose }: { onClose: () => void }) {
  const [config, setConfig] = useState<VisionConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testingSection, setTestingSection] = useState<SectionKey | null>(null)
  const [testResult, setTestResult] = useState<VisionTestResponse | null>(null)
  const [statusMsg, setStatusMsg] = useState<
    { kind: 'ok' | 'err'; text: string } | null
  >(null)

  useEffect(() => {
    let cancelled = false
    getVisionConfig()
      .then((c) => {
        if (!cancelled) setConfig(c)
      })
      .catch((e) => {
        if (!cancelled)
          setStatusMsg({
            kind: 'err',
            text: `Could not load config: ${e instanceof Error ? e.message : String(e)}`,
          })
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const updateField = (
    section: SectionKey,
    field: keyof VisionConfigSection,
    value: string,
  ) => {
    setConfig((prev) => {
      if (!prev) return null
      return {
        ...prev,
        [section]: { ...prev[section], [field]: value },
      }
    })
  }

  const handleTest = async (section: SectionKey) => {
    if (!config) return
    setTestingSection(section)
    setStatusMsg(null)
    try {
      // Send the FULL config so the server can probe both sections in
      // one call if it wants. We use only the requested section's
      // result downstream though, since "Test" is per-section in the
      // UI to keep the user model simple.
      const result = await testVisionConfig(config)
      setTestResult((prev) => ({ ...(prev ?? {}), ...result }))
    } catch (e) {
      setTestResult((prev) => ({
        ...(prev ?? {}),
        [section]: {
          reachable: false,
          error: e instanceof Error ? e.message : String(e),
        },
      }))
    }
    setTestingSection(null)
  }

  const handleSave = async () => {
    if (!config) return
    setSaving(true)
    setStatusMsg(null)
    try {
      const updated = await updateVisionConfig(config)
      setConfig(updated)         // server returns redacted form after save
      setTestResult(null)        // model lists may be stale after URL/key change
      setStatusMsg({ kind: 'ok', text: 'Saved. No restart needed.' })
    } catch (e) {
      setStatusMsg({
        kind: 'err',
        text: `Save failed: ${e instanceof Error ? e.message : String(e)}`,
      })
    }
    setSaving(false)
  }

  if (loading) {
    return (
      <div className="bg-surface-secondary rounded-lg p-3 border border-border text-sm text-text-muted">
        Loading config…
      </div>
    )
  }

  if (!config) {
    return (
      <div className="bg-error/10 rounded-lg p-3 border border-error/30 text-sm text-error space-y-2">
        <p>Could not load Cortex Vision config.</p>
        {statusMsg && <p className="text-xs whitespace-pre-wrap">{statusMsg.text}</p>}
        <button
          onClick={onClose}
          className="px-2 py-1 text-xs rounded border border-border text-text-secondary cursor-pointer"
        >
          Close
        </button>
      </div>
    )
  }

  return (
    <div className="bg-surface-secondary rounded-lg p-4 border border-border space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">
            Configure Cortex Vision
          </h3>
          {config.config_path && (
            <p className="text-[11px] text-text-muted mt-0.5 font-mono">
              {String(config.config_path)}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary text-sm cursor-pointer"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      <SectionEditor
        section="describer"
        value={config.describer}
        onChange={(field, value) => updateField('describer', field, value)}
        onTest={() => handleTest('describer')}
        testing={testingSection === 'describer'}
        testResult={testResult?.describer}
      />

      <SectionEditor
        section="transcribe"
        value={config.transcribe}
        onChange={(field, value) => updateField('transcribe', field, value)}
        onTest={() => handleTest('transcribe')}
        testing={testingSection === 'transcribe'}
        testResult={testResult?.transcribe}
      />

      {statusMsg && (
        <div
          className={`text-xs rounded-md p-2 whitespace-pre-wrap ${
            statusMsg.kind === 'ok'
              ? 'bg-success/10 text-success border border-success/30'
              : 'bg-error/10 text-error border border-error/30'
          }`}
        >
          {statusMsg.text}
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
        <button
          onClick={onClose}
          disabled={saving}
          className="px-3 py-1.5 text-xs rounded border border-border text-text-secondary hover:bg-surface-tertiary disabled:opacity-50 cursor-pointer"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-4 py-1.5 text-xs rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-50 cursor-pointer"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

function SectionEditor({
  section,
  value,
  onChange,
  onTest,
  testing,
  testResult,
}: {
  section: SectionKey
  value: VisionConfigSection
  onChange: (field: keyof VisionConfigSection, value: string) => void
  onTest: () => void
  testing: boolean
  testResult?: VisionTestSection
}) {
  const labels = SECTION_LABELS[section]
  const availableModels = testResult?.available_models ?? []
  // Show user's current model first, then any new ones the test
  // surfaced that aren't already represented.
  const modelOptions = [
    value.model,
    ...availableModels.filter((m) => m !== value.model),
  ].filter((m) => m && m.length > 0)

  return (
    <div className="space-y-3">
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          {labels.title}
        </h4>
        <p className="text-[11px] text-text-muted mt-0.5">{labels.subtitle}</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-2">
        <Field label="URL">
          <input
            type="text"
            value={value.url}
            onChange={(e) => onChange('url', e.target.value)}
            placeholder="http://localhost:1234/v1"
            className="w-full px-2 py-1.5 text-xs rounded bg-surface border border-border focus:border-accent focus:outline-none text-text-primary placeholder:text-text-muted font-mono"
          />
        </Field>

        <Field label="Model">
          {modelOptions.length > 1 ? (
            <select
              value={value.model}
              onChange={(e) => onChange('model', e.target.value)}
              className="w-full px-2 py-1.5 text-xs rounded bg-surface border border-border focus:border-accent focus:outline-none text-text-primary"
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={value.model}
              onChange={(e) => onChange('model', e.target.value)}
              placeholder="qwen2.5-vl-7b"
              className="w-full px-2 py-1.5 text-xs rounded bg-surface border border-border focus:border-accent focus:outline-none text-text-primary placeholder:text-text-muted font-mono"
            />
          )}
        </Field>

        <Field
          label="API key"
          hint={
            value.api_key === '***'
              ? 'Leave as *** to keep current. Type to replace; clear to remove.'
              : 'Optional. Leave empty for unauthenticated endpoints.'
          }
        >
          <input
            type="password"
            value={value.api_key}
            onChange={(e) => onChange('api_key', e.target.value)}
            placeholder=""
            className="w-full px-2 py-1.5 text-xs rounded bg-surface border border-border focus:border-accent focus:outline-none text-text-primary font-mono"
          />
        </Field>

        <div className="flex items-end gap-2">
          <button
            onClick={onTest}
            disabled={testing}
            className="px-3 py-1.5 text-xs rounded border border-border text-text-secondary hover:bg-surface-tertiary disabled:opacity-50 cursor-pointer"
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          {testResult && <TestResultPill result={testResult} />}
        </div>
      </div>
    </div>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wide text-text-muted">
        {label}
      </span>
      <div className="mt-1">{children}</div>
      {hint && <p className="text-[10px] text-text-muted mt-1">{hint}</p>}
    </label>
  )
}

function TestResultPill({ result }: { result: VisionTestSection }) {
  if (result.reachable) {
    const count = result.available_models?.length ?? 0
    return (
      <span className="text-xs px-2 py-1 rounded bg-success/15 text-success">
        ✓ Reachable{count ? ` · ${count} model${count === 1 ? '' : 's'}` : ''}
      </span>
    )
  }
  return (
    <span
      className="text-xs px-2 py-1 rounded bg-error/15 text-error truncate max-w-[260px]"
      title={result.error ?? 'unreachable'}
    >
      ✗ {result.error ?? 'Unreachable'}
    </span>
  )
}
