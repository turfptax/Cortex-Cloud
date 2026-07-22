/**
 * Typed client for the cortex-vision plugin's HTTP API.
 *
 * Everything is proxied through the cortex-desktop Hub at
 * /api/video/* (the proxy lives in hub/backend/routers/video.py).
 * Shapes mirror cortex-vision/docs/DATA_MODEL.md verbatim — keep
 * this file synced when the upstream contract evolves.
 */
import { apiFetch } from './api'

export type VideoMode = 'live' | 'file' | 'journal'

export type SessionStatus =
  | 'queued'
  | 'capturing'
  | 'processing'
  | 'describing'
  | 'narrating'
  | 'complete'
  | 'error'

export interface SceneEntry {
  index: number
  start_s: number
  end_s: number
  keyframe_paths: string[]
  description: string
  describer_model: string
  spoken_text: string | null
  objects: string[]
  similarity: number
  trigger_method: string
}

export interface TranscriptEntry {
  timestamp: string
  text: string
  duration_s: number
  latency_ms: number
  rms: number
  chunk_index: number
}

export interface VideoSession {
  id: string
  mode: VideoMode
  source: Record<string, unknown>
  status: SessionStatus
  project_id: string | null
  started_at: string
  ended_at: string | null
  duration_s: number | null
  scenes: SceneEntry[]
  narrative: string | null
  transcript: TranscriptEntry[]
  pushed_to_overseer: boolean
  error: string | null
  progress: { current_scene?: number; total_scenes?: number; [key: string]: unknown }
}

/** Returned by POST /api/video/jobs and POST /api/video/jobs/upload.
 * The upload endpoint additionally reports bytes_uploaded. */
export interface CreateJobResponse {
  session_id: string
  status: SessionStatus
  poll_url: string
  bytes_uploaded?: number
}

/** Body for POST /api/video/jobs */
export interface CreateJobRequest {
  source: string                                 // URL or local file path
  mode?: VideoMode                               // defaults to "file"
  project_id?: string | null
  push_to_overseer?: boolean
  transcribe_audio?: boolean
  keyframes_per_scene?: number
  describer_model?: string | null
  narrative_model?: string | null
}

/** Lightweight shape returned by GET /api/video/sessions (list view).
 * The hydrated full shape comes from GET /api/video/sessions/{id}. */
export interface SessionListEntry {
  id: string
  mode: VideoMode
  status: SessionStatus
  project_id: string | null
  started_at: string
  ended_at: string | null
  duration_s: number | null
}

// ---------------------------------------------------------------------------
// Calls — all routed via apiFetch -> /api/video/* through the Hub proxy.
// ---------------------------------------------------------------------------

export async function createJob(req: CreateJobRequest): Promise<CreateJobResponse> {
  return apiFetch<CreateJobResponse>('/video/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export interface UploadJobOptions {
  mode?: VideoMode
  project_id?: string | null
  push_to_overseer?: boolean
  transcribe_audio?: boolean
  keyframes_per_scene?: number
  describer_model?: string | null
  narrative_model?: string | null
  /** Reports bytes-uploaded as the upload streams. The browser doesn't
   * surface upload progress on fetch(); this hook uses XHR under the
   * hood for the progress event. */
  onProgress?: (loaded: number, total: number) => void
}

/** Multipart upload to POST /api/video/jobs/upload (Phase 3 + journal mode).
 *
 * Uses XMLHttpRequest rather than fetch() so we can report upload progress
 * via the `onProgress` callback. Browsers don't expose upload progress via
 * fetch() yet (the spec exists but no major browser ships it).
 */
export function uploadJob(
  blob: Blob,
  filename: string,
  options: UploadJobOptions = {},
): Promise<CreateJobResponse> {
  return new Promise((resolve, reject) => {
    const fd = new FormData()
    fd.append('file', blob, filename)
    if (options.mode) fd.append('mode', options.mode)
    if (options.project_id) fd.append('project_id', options.project_id)
    if (options.push_to_overseer != null)
      fd.append('push_to_overseer', String(options.push_to_overseer))
    if (options.transcribe_audio != null)
      fd.append('transcribe_audio', String(options.transcribe_audio))
    if (options.keyframes_per_scene != null)
      fd.append('keyframes_per_scene', String(options.keyframes_per_scene))
    if (options.describer_model)
      fd.append('describer_model', options.describer_model)
    if (options.narrative_model)
      fd.append('narrative_model', options.narrative_model)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/video/jobs/upload')

    if (options.onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) options.onProgress!(e.loaded, e.total)
      })
    }

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch (e) {
          reject(new Error(`Bad upload response: ${e}`))
        }
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.responseText}`))
      }
    })
    xhr.addEventListener('error', () =>
      reject(new Error('Upload network error'))
    )
    xhr.addEventListener('abort', () => reject(new Error('Upload aborted')))

    xhr.send(fd)
  })
}

export async function getSession(sessionId: string): Promise<VideoSession> {
  return apiFetch<VideoSession>(`/video/sessions/${encodeURIComponent(sessionId)}`)
}

export async function listSessions(opts?: {
  limit?: number
  mode?: VideoMode
}): Promise<SessionListEntry[]> {
  const qs = new URLSearchParams()
  if (opts?.limit) qs.set('limit', String(opts.limit))
  if (opts?.mode) qs.set('mode', opts.mode)
  const q = qs.toString()
  return apiFetch<SessionListEntry[]>(`/video/sessions${q ? `?${q}` : ''}`)
}

/** Build a frame URL pointing at the proxy. The proxy streams the JPEG
 * straight from the sidecar; <img src> works directly. */
export function frameUrl(
  sessionId: string,
  sceneIndex: number,
  frameIndex: number,
): string {
  return `/api/video/jobs/${encodeURIComponent(sessionId)}/frame/${sceneIndex}/${frameIndex}`
}

// ---------------------------------------------------------------------------
// Live mode (Phase 4) — camera selection, start/stop, WebSocket protocol
// ---------------------------------------------------------------------------

export interface CameraInfo {
  index: number
  name?: string
  /** Per cortex-vision's describe_cameras() — opens each device briefly
   * to read the native capture format. */
  native_resolution?: [number, number]
  native_fps?: number
  // Forward-compatible: cortex-vision may add fields
  [key: string]: unknown
}

export interface LiveStartRequest {
  camera_index?: number
  resolution?: [number, number]
  project_id?: string | null
  threshold?: number
  pixel_diff_threshold?: number
  structural_threshold?: number
  steady_interval?: number
  min_scene_gap?: number
  describer_model?: string | null
  /** Phase 7 audio capture (cortex-vision v0.4.0+).
   *   - null: video-only, no audio capture
   *   - "desktop": WASAPI loopback (records system audio)
   *   - int: sounddevice index from /live/audio-devices
   *   - other string: name substring match against the device list */
  audio_source?: number | string | null
  /** Run the captured audio through whisper after Stop. Off by
   * default; same UX shape as FileMode. */
  transcribe_audio?: boolean
}

/** One entry from GET /api/video/live/audio-devices. cortex-vision
 * exposes both real input devices and the synthetic "desktop" loopback. */
export interface AudioDevice {
  index: number | null              // null for the synthetic "desktop" entry
  name: string
  kind: 'desktop' | 'input' | string
  channels?: number
  default_sample_rate?: number
  // Forward-compatible — cortex-vision may add fields
  [key: string]: unknown
}

export interface LiveStartResponse {
  session_id: string
  status: SessionStatus
  ws_url: string
  stop_url: string
  config: {
    camera_index: number
    resolution: [number, number]
  }
}

export interface LiveStopResponse {
  stopped: boolean
  final_status: Record<string, unknown>
}

export type LiveStatus =
  | { is_running: false }
  | (Record<string, unknown> & { is_running?: true })

export async function listCameras(): Promise<CameraInfo[]> {
  const r = await apiFetch<{ cameras: CameraInfo[] }>('/video/live/cameras')
  return r.cameras ?? []
}

export async function listAudioDevices(): Promise<AudioDevice[]> {
  const r = await apiFetch<{ devices?: AudioDevice[]; audio_devices?: AudioDevice[] }>(
    '/video/live/audio-devices',
  )
  // Tolerate either key name on the wire — cortex-vision's spec
  // says "audio-devices" plural; safe to accept both.
  return r.devices ?? r.audio_devices ?? []
}

export async function liveStart(
  req: LiveStartRequest = {},
): Promise<LiveStartResponse> {
  return apiFetch<LiveStartResponse>('/video/live/start', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function liveStop(): Promise<LiveStopResponse> {
  return apiFetch<LiveStopResponse>('/video/live/stop', { method: 'POST' })
}

export async function liveStatus(): Promise<LiveStatus> {
  return apiFetch<LiveStatus>('/video/live/status')
}

// --- WebSocket event protocol (per cortex_vision/pipeline/live.py) ----------

export type LiveEvent =
  | { type: 'started'; session_id: string; camera_index: number; resolution: [number, number] }
  | {
      type: 'scene'
      scene_index: number
      change_type: 'scene_change' | 'update'
      thumbnail_url: string
      trigger_method: string
      similarity: number
    }
  | {
      type: 'described'
      scene_index: number
      description: string
      describer_model: string
    }
  | {
      type: 'stats'
      fps: number
      frames: number
      scene_count: number
      elapsed_s: number
      [key: string]: unknown
    }
  | {
      type: 'stopped'
      session_id: string
      scene_count: number
      duration_s: number
    }
  | { type: 'error'; message: string }
  // Phase 7 audio events (cortex-vision v0.4.0+)
  | {
      type: 'audio_level'
      rms: number                          // 0..1
      peak: number                         // 0..1
      [key: string]: unknown
    }
  | {
      type: 'transcribing'
      message?: string
    }
  | {
      type: 'transcribed'
      segment_count: number
      scenes_with_audio: number
    }
  | { type: 'transcribe_skipped'; reason?: string }
  | { type: 'transcribe_failed'; error?: string; message?: string }
  // Forward-compatible: ignore unknown future types
  | { type: string; [key: string]: unknown }

/** Build the WebSocket URL pointing at the Hub's proxied /live/ws path.
 * Picks ws:// vs wss:// from the page's protocol so it works behind TLS. */
export function liveWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/video/live/ws`
}

// ---------------------------------------------------------------------------
// Vision config (Phase 6 — Settings → Plugins → Configure)
//
// API keys round-trip with the sentinel "***" when the server already
// has a key set. Submit "***" to keep the existing key, "" to clear,
// anything else to set. The server also accepts partial updates — we
// always send the full object since we have it loaded anyway.
// ---------------------------------------------------------------------------

export interface VisionConfigSection {
  url: string
  model: string
  api_key: string                      // "***" === existing key kept
}

export interface VisionConfig {
  describer: VisionConfigSection
  transcribe: VisionConfigSection
  /** Phase 4 live-mode tuning knobs. We pass through unchanged on save
   * so users don't lose their tuning when adjusting describer/transcribe. */
  live?: Record<string, unknown>
  config_path?: string
  // Forward-compatible — cortex-vision may add sections we don't render
  [key: string]: unknown
}

/** Per-section result from POST /api/video/config/test. */
export interface VisionTestSection {
  reachable: boolean
  available_models?: string[]
  error?: string | null
}

export type VisionTestResponse = {
  describer?: VisionTestSection
  transcribe?: VisionTestSection
} & {
  [key: string]: VisionTestSection | undefined
}

export async function getVisionConfig(): Promise<VisionConfig> {
  return apiFetch<VisionConfig>('/video/config')
}

/** Send the full config object back. Server treats it as a patch —
 * atomic write, no restart needed (sidecar re-reads on every request). */
export async function updateVisionConfig(
  patch: VisionConfig,
): Promise<VisionConfig> {
  return apiFetch<VisionConfig>('/video/config', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

/** Probe connectivity without saving. Returns `available_models` per
 * section when reachable so the form can populate model dropdowns. */
export async function testVisionConfig(
  proposed: VisionConfig,
): Promise<VisionTestResponse> {
  return apiFetch<VisionTestResponse>('/video/config/test', {
    method: 'POST',
    body: JSON.stringify(proposed),
  })
}

// ---------------------------------------------------------------------------
// Helpers — status taxonomy
// ---------------------------------------------------------------------------

export const TERMINAL_STATUSES: ReadonlySet<SessionStatus> = new Set([
  'complete',
  'error',
])

export function isTerminal(status: SessionStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

/** Display label + tailwind color for each status. */
export const STATUS_DISPLAY: Record<SessionStatus, { label: string; color: string }> = {
  queued: { label: 'Queued', color: 'bg-text-muted/30 text-text-secondary' },
  capturing: { label: 'Capturing', color: 'bg-amber-500/15 text-amber-400' },
  processing: { label: 'Processing', color: 'bg-amber-500/15 text-amber-400' },
  describing: { label: 'Describing', color: 'bg-accent/15 text-accent-hover' },
  narrating: { label: 'Narrating', color: 'bg-accent/15 text-accent-hover' },
  complete: { label: 'Complete', color: 'bg-success/15 text-success' },
  error: { label: 'Error', color: 'bg-error/15 text-error' },
}
