import { useEffect, useRef, useState, useCallback } from 'react'
import {
  createJob,
  getSession,
  isTerminal,
  uploadJob,
  type CreateJobRequest,
  type UploadJobOptions,
  type VideoMode,
  type VideoSession,
} from '../lib/videoApi'

const POLL_INTERVAL_MS = 2_000

/** Submit a video job and poll its status until it reaches a terminal state.
 *
 * Refresh-resilient: if a session_id is passed in (e.g. from URL state or
 * localStorage), the hook resumes polling without re-submitting. */
export function useVideoJob(initialSessionId?: string) {
  const [sessionId, setSessionId] = useState<string | null>(
    initialSessionId ?? null
  )
  const [session, setSession] = useState<VideoSession | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollTimerRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const pollOnce = useCallback(
    async (id: string) => {
      try {
        const s = await getSession(id)
        setSession(s)
        if (!isTerminal(s.status)) {
          pollTimerRef.current = window.setTimeout(
            () => pollOnce(id),
            POLL_INTERVAL_MS
          )
        }
      } catch (e) {
        // Don't kill the polling loop on a transient error — just back off
        // one tick. If the sidecar is down we'll see it in the proxy 503.
        const msg = e instanceof Error ? e.message : String(e)
        setError(msg)
        pollTimerRef.current = window.setTimeout(
          () => pollOnce(id),
          POLL_INTERVAL_MS * 2
        )
      }
    },
    []
  )

  // Resume polling on mount when a session_id is provided
  useEffect(() => {
    if (sessionId) {
      pollOnce(sessionId)
    }
    return () => stopPolling()
    // intentionally only on the first mount-with-sessionId
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  const submit = useCallback(
    async (req: CreateJobRequest): Promise<string | null> => {
      stopPolling()
      setSubmitting(true)
      setError(null)
      setSession(null)
      try {
        const resp = await createJob(req)
        setSessionId(resp.session_id)
        // Optimistic seed so the UI has something to render before the first poll
        setSession({
          id: resp.session_id,
          mode: req.mode ?? 'file',
          source: { kind: 'url', url: req.source },
          status: resp.status,
          project_id: req.project_id ?? null,
          started_at: new Date().toISOString(),
          ended_at: null,
          duration_s: null,
          scenes: [],
          narrative: null,
          transcript: [],
          pushed_to_overseer: false,
          error: null,
          progress: {},
        })
        pollOnce(resp.session_id)
        return resp.session_id
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        setError(msg)
        return null
      } finally {
        setSubmitting(false)
      }
    },
    [pollOnce, stopPolling]
  )

  const submitUpload = useCallback(
    async (
      blob: Blob,
      filename: string,
      options: UploadJobOptions & { mode?: VideoMode } = {},
    ): Promise<string | null> => {
      stopPolling()
      setSubmitting(true)
      setError(null)
      setSession(null)
      try {
        const resp = await uploadJob(blob, filename, options)
        setSessionId(resp.session_id)
        // Optimistic seed before the first poll
        setSession({
          id: resp.session_id,
          mode: options.mode ?? 'journal',
          source: { kind: 'upload', filename },
          status: resp.status,
          project_id: options.project_id ?? null,
          started_at: new Date().toISOString(),
          ended_at: null,
          duration_s: null,
          scenes: [],
          narrative: null,
          transcript: [],
          pushed_to_overseer: false,
          error: null,
          progress: {},
        })
        pollOnce(resp.session_id)
        return resp.session_id
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        return null
      } finally {
        setSubmitting(false)
      }
    },
    [pollOnce, stopPolling],
  )

  const reset = useCallback(() => {
    stopPolling()
    setSessionId(null)
    setSession(null)
    setError(null)
  }, [stopPolling])

  return {
    sessionId,
    session,
    submitting,
    error,
    submit,
    submitUpload,
    reset,
  }
}
