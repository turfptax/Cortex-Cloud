/**
 * Slice 5 CP3+CP4: Journal tab.
 *
 * The single home for anything reflective in the Hub. Three
 * stacked sections, top → bottom:
 *
 *   1. YOUR JOURNAL — free-form textarea + recent entries you've
 *      written. Multiple per day allowed (Tory's call: simpler data
 *      shape, no edit-state to manage). Auto-included in temporal
 *      narrative prompts when they fall in the period being
 *      summarized.
 *
 *   2. TEMPORAL NARRATIVES — Daily / Weekly / Monthly Sonnet
 *      rollups produced by the loop on a 22:00-local schedule.
 *      Latest of each kind shown; click "All <kind>" for history.
 *      Per-kind "Generate now" button bypasses the time gate.
 *
 *   3. OVERSEER REFLECTIONS — the original tick-based first-person
 *      journal. Append-only by the overseer; read-along for the
 *      user.
 *
 * Per Tory's locked principle: this is a quiet memory layer. No
 * notifications, no streaks, no nags. Read what you want to read,
 * write when you want to write.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../../lib/api'

// ── Types ────────────────────────────────────────────────────────

export interface OverseerJournalEntry {
  id: number
  written_at: string
  local_written_at?: string  // Slice 9.4.1: ISO with offset
  instance_id: string
  triggered_by: string
  body: string
  provisionality: 'high' | 'med' | 'low'
  model: string
  cost_usd: number
}

interface HumanJournalEntry {
  id: number
  text: string
  entry_type: string
  created_at: string
  local_created_at: string
}

interface TemporalNarrative {
  id: number
  kind: 'daily' | 'weekly' | 'monthly'
  period_start: string
  period_end: string
  period_label: string
  narrative: string
  cost_usd: number
  model: string
  triggered_by: string
  created_at: string
  local_created_at: string
}

interface ListHumanResp {
  ok: boolean
  entries?: HumanJournalEntry[]
  count?: number
  error?: string
}

interface ListTemporalResp {
  ok: boolean
  narratives?: TemporalNarrative[]
  count?: number
  error?: string
}

interface AddHumanResp {
  ok: boolean
  id?: number
  error?: string
}

interface GenerateTemporalResp {
  ok: boolean
  kind?: string
  period_label?: string
  narrative?: string
  cost_usd?: number
  model?: string
  latency_ms?: number
  error?: string
  skipped?: boolean
  reason?: string
}

// ── Helpers ──────────────────────────────────────────────────────

function fmtRelativeTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z')
  const ms = Date.now() - d.getTime()
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.round(days / 7)}w ago`
  if (days < 365) return `${Math.round(days / 30)}mo ago`
  return `${Math.round(days / 365)}y ago`
}

// Slice 9.4.1 (2026-05-16): use the shared time helper so EVERY
// timestamp display includes its tz suffix (CDT/CST/UTC/etc.). The
// old local `fmtLocalShort` helper used `slice(0, 16)` which silently
// dropped the offset — that pattern surfaced the "future-dated entry"
// confusion when an entry written after 19:00 CDT had its UTC string
// rendered as if it were local. See
// memory/feedback_time_always_local_with_tz.md.
import { fmtTime } from '../../lib/time'

// ── Section 1: Human journal ─────────────────────────────────────

type TranscribeStage =
  | 'idle' | 'queued' | 'normalizing' | 'transcribing'
  | 'ready' | 'error'

interface TranscribeStateBlock {
  in_progress: boolean
  stage: TranscribeStage
  progress_pct: number
  started_at: number | null
  finished_at: number | null
  model: string
  filename: string
  duration_s: number
  language: string
  latency_ms: number
  transcript: string | null
  error: string | null
}

interface TranscribeResp {
  ok: boolean
  // CP4 happy path: 202 Accepted, kick off polling
  status?: 'transcribing' | 'transcribe_already_running'
          | 'model_downloading' | 'model_already_downloading'
  message?: string
  transcribe_state?: TranscribeStateBlock
  // Model-download specifics
  bytes_total?: number
  bytes_downloaded?: number
  // Legacy synchronous shape (pre-CP4) — kept so the response
  // parser is forward+backward compatible.
  transcript?: string
  detail?: { error?: string; message?: string } | string
}

interface TranscribeStatusResp {
  ok: boolean
  binary_present: boolean
  ffmpeg_installed: boolean
  model: string
  model_present: boolean
  gpu_capable?: boolean
  binary_backends?: string[]
  model_download: {
    in_progress: boolean
    bytes_downloaded: number
    bytes_total: number
    error?: string | null
  }
  transcribe_state?: TranscribeStateBlock
  // dev.14: surfaces native-fault state from the backend so the
  // UI can render a remediation banner. last_hard_crash is one of
  // 'ILLEGAL_INSTRUCTION' | 'STACK_BUFFER_OVERRUN' | 'ACCESS_VIOLATION'
  // when whisper-cli has died with that signature this session.
  runtime_flags?: {
    force_cpu: boolean
    last_hard_crash: string | null
    last_hard_crash_at: number | null
    fallback_succeeded: boolean
  }
}

// Approximate on-disk size of each GGML model — used in the
// "downloading model" UI affordance so the user knows whether
// they're waiting for a 75MB tiny or a 3GB large. Pulled from
// the HF model files at HuggingFace/ggerganov/whisper.cpp; small
// drift (a few MB) is fine for a label.
const MODEL_SIZE_LABELS: Record<string, string> = {
  'tiny': '~75MB',
  'tiny.en': '~75MB',
  'base': '~140MB',
  'base.en': '~140MB',
  'small': '~470MB',
  'small.en': '~470MB',
  'medium': '~1.5GB',
  'medium.en': '~1.5GB',
  'large-v1': '~3GB',
  'large-v2': '~3GB',
  'large-v3': '~3GB',
  'large-v3-turbo': '~1.5GB',
  'turbo': '~1.5GB',
  'large': '~3GB',
}

function modelSizeLabel(model: string): string {
  return MODEL_SIZE_LABELS[model] || ''
}

function HumanJournalSection() {
  const [entries, setEntries] = useState<HumanJournalEntry[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  // Slice 7 transcribe state. CP4 added live percentage during
  // 'transcribing' (whisper-cli's stderr progress is parsed by the
  // backend and exposed via /api/transcribe/status).
  const [transcribeStage, setTranscribeStage] = useState<
    null | 'extracting' | 'transcribing'
    | 'downloading-model' | 'queued-pending-model'>(null)
  const [transcribePct, setTranscribePct] = useState<number>(0)
  const [downloadProgress, setDownloadProgress] = useState<{
    bytes: number; total: number
  } | null>(null)
  // dev.14: pulled from /api/transcribe/status so the UI labels
  // ("Downloading large-v3 model… ~3GB") match the actual configured
  // model instead of hardcoding "large-v3" / "3GB". Empty string
  // until the first status fetch lands.
  const [activeModel, setActiveModel] = useState<string>('')
  // dev.14: hard-crash banner. When the backend has seen a native
  // fault from whisper-cli (illegal instruction / stack overrun /
  // AV) we render a persistent banner with remediation steps. The
  // backend keeps this set across runs in this Hub process; it
  // resets on Hub restart or on a successful run after fallback.
  const [hardCrash, setHardCrash] = useState<{
    signature: string; fallbackSucceeded: boolean
  } | null>(null)
  const pendingFileRef = useRef<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await apiFetch<ListHumanResp>('/overseer/human-journal?limit=200')
      setEntries(r.entries || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // CP4 refresh resilience: on mount, ask the backend whether a
  // transcription is mid-flight (e.g., user reloaded the page
  // during a long whisper-cli run). If so, rebind the polling
  // loop so the percentage display picks up where it left off
  // and the transcript still lands when the run finishes.
  // Also: pull the configured model name + any sticky hard-crash
  // signature so the model-download UI labels match config and
  // the remediation banner renders if a prior run died natively.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const s = await fetch('/api/transcribe/status').then(
          (r) => r.json() as Promise<TranscribeStatusResp>)
        if (cancelled) return
        if (s?.model) setActiveModel(s.model)
        const rf = s?.runtime_flags
        if (rf?.last_hard_crash) {
          setHardCrash({
            signature: rf.last_hard_crash,
            fallbackSucceeded: !!rf.fallback_succeeded,
          })
        }
        const ts = s?.transcribe_state
        if (ts && ts.in_progress) {
          setTranscribePct(ts.progress_pct || 0)
          await pollTranscribeProgress()
        }
      } catch {
        // No backend reachable — nothing to resume.
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSave = async () => {
    const trimmed = text.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    try {
      const r = await apiFetch<AddHumanResp>(
        '/overseer/human-journal',
        { method: 'POST', body: JSON.stringify({ text: trimmed, entry_type: 'free' }) },
      )
      if (!r.ok) {
        setError(r.error || 'save failed')
        return
      }
      setText('')
      void refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this entry?')) return
    try {
      await apiFetch('/overseer/human-journal/delete', {
        method: 'POST', body: JSON.stringify({ id }),
      })
      void refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    }
  }

  // CP4: poll /api/transcribe/status during an in-flight
  // background transcription. Updates the percentage, then
  // when stage === 'ready' pre-fills the textarea with the
  // transcript and clears the loading state.
  //
  // Doubles as the refresh-resilience hook — if the user reloads
  // the page mid-transcription, mountTranscribeResume() (below)
  // calls this and the UI rebinds to the still-running job.
  const pollTranscribeProgress = async () => {
    setTranscribeStage('transcribing')
    while (true) {
      await new Promise((r) => setTimeout(r, 1500))
      try {
        const s = await fetch('/api/transcribe/status').then(
          (r) => r.json() as Promise<TranscribeStatusResp>)
        const rf = s?.runtime_flags
        if (rf?.last_hard_crash) {
          setHardCrash({
            signature: rf.last_hard_crash,
            fallbackSucceeded: !!rf.fallback_succeeded,
          })
        }
        const ts = s?.transcribe_state
        if (!ts) {
          setTranscribeStage(null)
          return
        }
        // Map backend stage to UI stage
        if (ts.stage === 'normalizing') {
          setTranscribeStage('extracting')
        } else if (ts.stage === 'transcribing') {
          setTranscribeStage('transcribing')
          setTranscribePct(ts.progress_pct || 0)
        } else if (ts.stage === 'ready') {
          const transcript = (ts.transcript || '').trim()
          if (transcript) {
            setText((prev) =>
              prev.trim()
                ? prev.trimEnd() + '\n\n' + transcript
                : transcript)
          }
          setTranscribeStage(null)
          setTranscribePct(0)
          return
        } else if (ts.stage === 'error') {
          setError(ts.error || 'transcription failed')
          setTranscribeStage(null)
          setTranscribePct(0)
          return
        } else if (!ts.in_progress) {
          // Some transient state we don't recognize and the run
          // isn't active — bail.
          setTranscribeStage(null)
          setTranscribePct(0)
          return
        }
      } catch (e: any) {
        // Don't bail on a transient poll error; keep trying. If
        // the network's truly down, the user can dismiss + retry.
      }
    }
  }

  // Slice 7 CP2: voice transcription via local whisper.cpp.
  // Two paths:
  //   1. Happy path — model present, transcribes immediately.
  //   2. First-run path — backend reports the GGML model isn't
  //      downloaded yet. Hub kicks off a background download;
  //      we poll /api/transcribe/status for progress, then
  //      retry the upload when the model lands.
  const pollDownloadAndRetry = async () => {
    setTranscribeStage('downloading-model')
    while (true) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const s = await fetch('/api/transcribe/status').then(
          (r) => r.json() as Promise<TranscribeStatusResp>)
        const dl = s?.model_download
        if (dl) {
          setDownloadProgress({
            bytes: dl.bytes_downloaded || 0,
            total: dl.bytes_total || 0,
          })
        }
        if (dl?.error) {
          setError('Model download failed: ' + dl.error)
          setTranscribeStage(null)
          setDownloadProgress(null)
          return
        }
        if (s?.model_present) {
          // Download done — retry the file the user gave us.
          setDownloadProgress(null)
          const pending = pendingFileRef.current
          if (pending) {
            pendingFileRef.current = null
            await handleTranscribe(pending)
          } else {
            setTranscribeStage(null)
          }
          return
        }
      } catch (e: any) {
        // Don't bail on transient poll errors — keep trying for a
        // little while; user can refresh if it never recovers.
        // (intentional no-op)
      }
    }
  }

  const handleTranscribe = async (file: File) => {
    setError(null)
    const lower = file.name.toLowerCase()
    const isVideo = /\.(mp4|mov|webm|mkv|avi|mpg|mpeg|m4v|3gp)$/.test(lower)
    setTranscribeStage(isVideo ? 'extracting' : 'transcribing')
    try {
      const form = new FormData()
      form.append('file', file)
      const resp = await fetch('/api/transcribe', {
        method: 'POST', body: form,
      })
      const r: TranscribeResp = await resp.json()
        .catch(() => ({ ok: false }))

      // First-run model-download path
      if (resp.ok && !r.ok && r.status?.startsWith('model_')) {
        pendingFileRef.current = file
        if (r.bytes_total || r.bytes_downloaded) {
          setDownloadProgress({
            bytes: r.bytes_downloaded || 0,
            total: r.bytes_total || 0,
          })
        }
        await pollDownloadAndRetry()
        return
      }

      // CP4 happy path: backend kicked off a background runner
      // and returned 202 with status: "transcribing". Switch to
      // polling — progress percentage will surface in the UI as
      // the run advances; transcript pre-fills when stage='ready'.
      if (resp.ok && !r.ok && r.status === 'transcribing') {
        setTranscribePct(r.transcribe_state?.progress_pct || 0)
        await pollTranscribeProgress()
        return
      }

      if (resp.ok && !r.ok && r.status === 'transcribe_already_running') {
        // Bind to the existing run and watch it finish — common
        // when the user uploaded, refreshed, then uploaded again.
        await pollTranscribeProgress()
        return
      }

      if (!resp.ok || !r.ok) {
        const detail = r.detail
        const msg = typeof detail === 'string'
          ? detail
          : detail?.message || r.message
              || `transcribe failed (${resp.status})`
        setError(msg)
        return
      }

      // Legacy synchronous path (kept for safety; backend always
      // returns 202 for transcribe in CP4+, but a clean 200 with
      // transcript still works if anyone wires that shape later).
      const transcript = (r.transcript || '').trim()
      if (transcript) {
        setText((prev) => {
          if (!prev.trim()) return transcript
          return prev.trimEnd() + '\n\n' + transcript
        })
      }
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setTranscribeStage(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const visibleEntries = showAll ? entries : entries.slice(0, 5)

  return (
    <section className="space-y-3">
      <header>
        <h3 className="text-base font-semibold text-text-primary">
          Your journal
        </h3>
        <p className="text-xs text-text-muted mt-1">
          Free-form notes — what you're thinking, what you're noticing.
          Auto-included in the daily/weekly/monthly narratives below
          when they fall in the period being summarized. Multiple per
          day is fine.
        </p>
      </header>

      <div className="space-y-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              void handleSave()
            }
          }}
          placeholder={
            transcribeStage === 'extracting'
              ? 'Normalizing audio…'
              : transcribeStage === 'transcribing'
                ? (transcribePct > 0
                    ? `Transcribing locally with whisper.cpp… ${transcribePct}%`
                    : 'Transcribing locally with whisper.cpp…')
                : transcribeStage === 'downloading-model'
                  ? (() => {
                      const m = activeModel || 'whisper'
                      const sz = modelSizeLabel(activeModel)
                      const sizeSuffix = sz ? `, ${sz}` : ''
                      if (downloadProgress && downloadProgress.total) {
                        const pct = Math.round(
                          downloadProgress.bytes /
                          downloadProgress.total * 100)
                        return `Downloading ${m} model (${pct}% — one-time setup${sizeSuffix})…`
                      }
                      return `Downloading ${m} model — one-time setup${sizeSuffix}…`
                    })()
                  : "What's on your mind? (Cmd/Ctrl+Enter to save)"
          }
          rows={3}
          disabled={!!transcribeStage}
          className="w-full px-3 py-2 bg-surface-tertiary text-text-primary text-sm rounded-md border border-border focus:outline-none focus:border-accent resize-y placeholder:text-text-muted/60 disabled:opacity-60"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={handleSave}
            disabled={busy || !text.trim() || !!transcribeStage}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent text-white hover:bg-accent-hover cursor-pointer disabled:opacity-40"
          >
            {busy ? 'Saving…' : 'Save entry'}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".mp3,.wav,.m4a,.ogg,.flac,.aac,.opus,.mp4,.mov,.webm,.mkv,.avi,.mpeg,.mpg,.m4v,.3gp,audio/*,video/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void handleTranscribe(f)
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={busy || !!transcribeStage}
            title="Upload audio or video — local whisper.cpp transcription, file never leaves your machine"
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-40 inline-flex items-center gap-1"
          >
            <span>🎤</span>
            {transcribeStage === 'extracting'
              ? 'Extracting…'
              : transcribeStage === 'transcribing'
                ? (transcribePct > 0
                    ? `Transcribing ${transcribePct}%`
                    : 'Transcribing…')
                : transcribeStage === 'downloading-model'
                  ? (downloadProgress && downloadProgress.total
                      ? `Setup ${Math.round(
                          downloadProgress.bytes /
                          downloadProgress.total * 100)}%`
                      : 'Setup…')
                  : 'Voice'}
          </button>
          <span className="ml-auto text-[11px] text-text-muted">
            {entries.length} entr{entries.length === 1 ? 'y' : 'ies'} total
          </span>
        </div>
        {/* dev.14: hard-crash remediation banner. Sticky for the
            session — only goes away on Hub restart or successful
            run after fallback. Distinct color from the dismissable
            error banner so the user can tell them apart. */}
        {hardCrash && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200 leading-relaxed">
            <div className="font-medium mb-1">
              Voice transcription hit a CPU-instruction crash
              ({hardCrash.signature})
            </div>
            <div className="text-amber-200/80">
              {hardCrash.fallbackSucceeded
                ? "The Hub fell back to CPU-only mode and the run succeeded. Future transcriptions in this session will skip the GPU."
                : "The bundled whisper-cli binary uses CPU instructions your machine doesn't support (most often AVX-512 on Intel 12th-gen+ or consumer Ryzen). Update the Hub to v0.18.0-dev.14 or later — that release ships an AVX2-baseline binary that runs everywhere."}
            </div>
            <button
              onClick={() => setHardCrash(null)}
              className="mt-1 text-[10px] uppercase tracking-wider text-amber-300/70 hover:text-amber-300 cursor-pointer"
              title="Dismiss"
            >
              dismiss
            </button>
          </div>
        )}
        {/* Multi-line error banner. Renders setup instructions
            (e.g. install commands) without truncation. */}
        {error && (
          <div className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300 leading-relaxed whitespace-pre-wrap">
            {error}
            <button
              onClick={() => setError(null)}
              className="ml-3 text-[10px] uppercase tracking-wider text-red-300/70 hover:text-red-300 cursor-pointer"
              title="Dismiss"
            >
              dismiss
            </button>
          </div>
        )}
      </div>

      {entries.length > 0 && (
        <div className="space-y-2">
          <ul className="space-y-2">
            {visibleEntries.map((e) => (
              <li
                key={e.id}
                className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 group"
              >
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="text-[11px] text-text-muted">
                    {fmtTime(e.local_created_at, e.created_at)}
                  </span>
                  <span className="text-[10px] text-text-muted/60 ml-auto">
                    {fmtRelativeTime(e.created_at)}
                  </span>
                  <button
                    onClick={() => handleDelete(e.id)}
                    className="text-[11px] text-text-muted/40 hover:text-red-400 cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity"
                    title="Delete entry"
                  >
                    ✕
                  </button>
                </div>
                <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
                  {e.text}
                </p>
              </li>
            ))}
          </ul>
          {entries.length > 5 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer"
            >
              {showAll ? 'Show recent' : `Show all ${entries.length} entries`}
            </button>
          )}
        </div>
      )}
    </section>
  )
}

// ── Section 2: Temporal narratives ───────────────────────────────

const KIND_LABEL: Record<TemporalNarrative['kind'], string> = {
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
}

const KIND_ORDER: TemporalNarrative['kind'][] = ['daily', 'weekly', 'monthly']

function TemporalNarrativesSection() {
  const [narratives, setNarratives] = useState<TemporalNarrative[]>([])
  const [loading, setLoading] = useState(false)
  const [busyKind, setBusyKind] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [showAllKind, setShowAllKind] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await apiFetch<ListTemporalResp>('/overseer/temporal?limit=200')
      setNarratives(r.narratives || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleGenerate = async (kind: string) => {
    setBusyKind(kind)
    setError(null)
    try {
      const r = await apiFetch<GenerateTemporalResp>(
        '/overseer/temporal/generate',
        { method: 'POST', body: JSON.stringify({ kind, force: true }) },
      )
      if (!r.ok) {
        setError(r.error || 'generate failed')
        return
      }
      void refresh()
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setBusyKind(null)
    }
  }

  const toggleExpanded = useCallback((id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const byKind = (kind: TemporalNarrative['kind']) =>
    narratives.filter((n) => n.kind === kind)

  return (
    <section className="space-y-3">
      <header>
        <h3 className="text-base font-semibold text-text-primary">
          Temporal narratives
        </h3>
        <p className="text-xs text-text-muted mt-1">
          Daily / Weekly / Monthly Sonnet rollups. Loop fires daily at 22:00 local,
          weekly Sunday 22:00, monthly the 1st 22:00 (skipped if no daily in past
          14 days). Click "Generate now" to bypass the schedule.
        </p>
        {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
      </header>

      <div className="space-y-4">
        {KIND_ORDER.map((kind) => {
          const rows = byKind(kind)
          const showAll = showAllKind === kind
          const visible = showAll ? rows : rows.slice(0, 1)
          const isBusy = busyKind === kind
          return (
            <div key={kind} className="rounded-lg border border-border bg-surface-secondary/40 overflow-hidden">
              <div className="px-4 py-2.5 flex items-baseline gap-3 border-b border-border bg-surface-tertiary/30">
                <h4 className="text-sm font-semibold text-text-primary">
                  {KIND_LABEL[kind]}
                </h4>
                <span className="text-[11px] text-text-muted">
                  {rows.length} entr{rows.length === 1 ? 'y' : 'ies'}
                </span>
                {rows.length > 1 && (
                  <button
                    onClick={() => setShowAllKind(showAll ? null : kind)}
                    className="text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer"
                  >
                    {showAll ? 'Latest only' : `All ${kind}`}
                  </button>
                )}
                <button
                  onClick={() => handleGenerate(kind)}
                  disabled={isBusy || loading}
                  className="ml-auto text-[11px] uppercase tracking-wider text-accent-hover hover:text-accent cursor-pointer disabled:opacity-40"
                >
                  {isBusy ? 'Generating…' : 'Generate now'}
                </button>
              </div>
              {visible.length === 0 ? (
                <div className="px-4 py-6 text-xs text-text-muted text-center italic">
                  {loading
                    ? 'Loading…'
                    : `No ${kind} narrative yet. Click "Generate now" to create one or wait for the loop's 22:00 local trigger.`}
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {visible.map((n) => (
                    <TemporalEntry
                      key={n.id}
                      n={n}
                      expanded={expandedIds.has(n.id)}
                      toggleExpanded={() => toggleExpanded(n.id)}
                    />
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function TemporalEntry({
  n,
  expanded,
  toggleExpanded,
}: {
  n: TemporalNarrative
  expanded: boolean
  toggleExpanded: () => void
}) {
  // Truncate to first paragraph by default — same pattern as
  // ProjectsTab's narrative block.
  const paragraphs = n.narrative.split(/\n\n+/).map((p) => p.trim()).filter(Boolean)
  const hasMore = paragraphs.length > 1
  const visibleBody = expanded ? n.narrative : (paragraphs[0] || n.narrative)
  return (
    <li className="px-4 py-3">
      <div className="flex items-baseline gap-3 mb-2">
        <span className="text-sm font-mono text-text-primary tabular-nums">
          {n.period_label}
        </span>
        <span className="text-[11px] text-text-muted">
          {fmtRelativeTime(n.created_at)}
          {n.triggered_by === 'manual' && (
            <span className="ml-1 text-text-muted/60">· manual</span>
          )}
        </span>
        <span className="ml-auto text-[10px] text-text-muted/60 tabular-nums">
          ${n.cost_usd?.toFixed(4) || '0'}
        </span>
      </div>
      <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
        {visibleBody}
      </p>
      {hasMore && (
        <button
          onClick={toggleExpanded}
          className="mt-1 text-[11px] uppercase tracking-wider text-accent-hover hover:text-accent cursor-pointer"
        >
          {expanded ? 'Show less ▴' : 'Read full ▾'}
        </button>
      )}
    </li>
  )
}

// ── Section 3: Overseer reflections (existing) ──────────────────

function OverseerReflectionsSection({
  entries,
  onRefresh,
}: {
  entries: OverseerJournalEntry[]
  onRefresh: () => void
}) {
  // Newest first. Default to 10 visible; expand to all on demand.
  const reversed = [...entries].reverse()
  const [showAll, setShowAll] = useState(false)
  const COLLAPSED = 10
  const visible = showAll ? reversed : reversed.slice(0, COLLAPSED)
  const hidden = reversed.length - visible.length

  return (
    <section className="space-y-3">
      <header className="flex items-baseline gap-3">
        <div>
          <h3 className="text-base font-semibold text-text-primary">
            Overseer reflections
          </h3>
          <p className="text-xs text-text-muted mt-1">
            The overseer's first-person notes at the end of notable
            ticks. Append-only — these are for future overseer instances
            to read at boot. You read along.
          </p>
        </div>
        <span className="ml-auto text-[11px] text-text-muted whitespace-nowrap">
          {reversed.length} total
        </span>
        <button
          onClick={onRefresh}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
        >
          Refresh
        </button>
      </header>
      {reversed.length === 0 ? (
        <div className="text-sm text-text-muted py-6 text-center italic">
          No reflections yet. They appear after the loop runs ticks
          with notable work.
        </div>
      ) : (
        <>
          <ul className="space-y-3">
            {visible.map((j) => (
              <OverseerJournalEntryView key={j.id} j={j} />
            ))}
          </ul>
          {hidden > 0 && (
            <button
              onClick={() => setShowAll(true)}
              className="w-full py-2 rounded-md text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary bg-surface-secondary/40 hover:bg-surface-secondary/70 cursor-pointer"
            >
              Show {hidden} more older reflections
            </button>
          )}
          {showAll && reversed.length > COLLAPSED && (
            <button
              onClick={() => setShowAll(false)}
              className="w-full py-2 rounded-md text-[11px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer"
            >
              Collapse to {COLLAPSED} most recent
            </button>
          )}
        </>
      )}
    </section>
  )
}

function OverseerJournalEntryView({ j }: { j: OverseerJournalEntry }) {
  const provColor =
    j.provisionality === 'high'
      ? 'bg-success/15 text-success'
      : j.provisionality === 'low'
        ? 'bg-red-500/15 text-red-400'
        : 'bg-text-muted/15 text-text-muted'
  return (
    <li className="rounded-lg border border-border bg-surface-secondary p-4">
      <div className="flex items-center gap-2 mb-2">
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${provColor}`}
          title="Overseer's self-reported confidence in this entry"
        >
          prov: {j.provisionality}
        </span>
        <span className="text-xs text-text-muted" title={fmtTime(j.local_written_at, j.written_at)}>
          {fmtTime(j.local_written_at, j.written_at)}
        </span>
        <span className="text-[11px] text-text-muted ml-auto truncate max-w-xs font-mono">
          {j.triggered_by} · {j.model}
        </span>
      </div>
      <p className="text-sm text-text-primary leading-relaxed whitespace-pre-wrap">
        {j.body}
      </p>
    </li>
  )
}

// ── Top-level export ─────────────────────────────────────────────

// Slice 9.4.2 (2026-05-17): Tory's UX directive — three stacked
// sections forced a lot of scrolling to reach the most recent records
// (especially overseer reflections, which can have 100+ entries since
// every notable tick writes one). Switched to sub-tabs so each section
// gets the full Hub-tab height, and the active sub-tab persists across
// reloads via localStorage. Inside each section, default-collapsed
// patterns ("Show N more …") keep the active sub-tab from growing
// unbounded over time.

type JournalSubTab = 'human' | 'temporal' | 'reflections'
const SUBTAB_STORAGE_KEY = 'cortex.overseer.journal.subtab'

function readPersistedSubTab(): JournalSubTab {
  try {
    const v = localStorage.getItem(SUBTAB_STORAGE_KEY)
    if (v === 'human' || v === 'temporal' || v === 'reflections') {
      return v
    }
  } catch {
    // localStorage may be unavailable in some embedded contexts
  }
  // Default to the human journal — it's the section Tory writes INTO,
  // so it's the most actionable surface to land on.
  return 'human'
}

interface SubTabDef {
  key: JournalSubTab
  label: string
  count?: number
}

export function JournalTab({
  overseerEntries,
  onRefreshOverseerJournal,
}: {
  overseerEntries: OverseerJournalEntry[]
  onRefreshOverseerJournal: () => void
}) {
  const [active, setActive] = useState<JournalSubTab>(readPersistedSubTab)

  const setAndPersist = useCallback((next: JournalSubTab) => {
    setActive(next)
    try {
      localStorage.setItem(SUBTAB_STORAGE_KEY, next)
    } catch {
      // ignore
    }
  }, [])

  const tabs: SubTabDef[] = [
    { key: 'human', label: 'Your journal' },
    { key: 'temporal', label: 'Temporal narratives' },
    {
      key: 'reflections',
      label: 'Overseer reflections',
      count: overseerEntries.length,
    },
  ]

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-4">
        <nav className="flex gap-0 border-b border-border">
          {tabs.map((t) => {
            const isActive = active === t.key
            return (
              <button
                key={t.key}
                onClick={() => setAndPersist(t.key)}
                className={
                  'px-4 py-2 text-sm font-medium border-b-2 -mb-px ' +
                  'transition-colors cursor-pointer ' +
                  (isActive
                    ? 'border-accent text-text-primary'
                    : 'border-transparent text-text-muted ' +
                      'hover:text-text-primary')
                }
              >
                {t.label}
                {t.count !== undefined && t.count > 0 && (
                  <span className="ml-1.5 text-[11px] text-text-muted">
                    ({t.count})
                  </span>
                )}
              </button>
            )
          })}
        </nav>
        {active === 'human' && <HumanJournalSection />}
        {active === 'temporal' && <TemporalNarrativesSection />}
        {active === 'reflections' && (
          <OverseerReflectionsSection
            entries={overseerEntries}
            onRefresh={onRefreshOverseerJournal}
          />
        )}
      </div>
    </div>
  )
}
