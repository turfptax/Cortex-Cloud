/**
 * Slice 9.4.1 — Time display helpers.
 *
 * Locked principle (memory/feedback_time_always_local_with_tz.md):
 *   "Time is of paramount importance and it needs to always show
 *    correct. We must treat time with the respect that it deserves
 *    and note the timezone always when we display time."
 *
 * The cortex DB schema (post-Slice-9.4.1) stores every timestamp in
 * two columns: a UTC `_at` column and a paired `local_<col>_at`
 * column with ISO-with-offset format (e.g. "2026-05-16T13:47:08-05:00").
 * Display surfaces should prefer the local variant and ALWAYS render
 * the timezone alongside the time.
 *
 * Use `fmtTime` for normal short displays. The other helpers are
 * lower-level building blocks.
 */

const TZ_ABBR: Record<string, string> = {
  '-05:00': 'CDT',  // America/Chicago, DST
  '-06:00': 'CST',  // America/Chicago, standard
  '-04:00': 'EDT',
  '-05:00:00': 'CDT',
  '-06:00:00': 'CST',
  '+00:00': 'UTC',
  Z: 'UTC',
}

/**
 * Convert an ISO-with-offset string to "YYYY-MM-DD HH:MM TZ" form.
 *
 * Input:  "2026-05-16T13:47:08-05:00"
 * Output: "2026-05-16 13:47 CDT"
 *
 * Unknown offsets fall through to "YYYY-MM-DD HH:MM -05:00" form
 * (raw offset still visible). Empty / null returns "".
 */
export function fmtLocalWithTz(iso: string | null | undefined): string {
  if (!iso) return ''
  // Match "YYYY-MM-DDTHH:MM(:SS)? <tz>"
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(.*)$/)
  if (!m) return iso  // fall through unchanged
  const [, date, time, tz] = m
  const trimmedTz = (tz || '').trim()
  const abbr = TZ_ABBR[trimmedTz] || trimmedTz || ''
  return abbr ? `${date} ${time} ${abbr}` : `${date} ${time}`
}

/**
 * Convert a naked UTC string ("YYYY-MM-DD HH:MM:SS", no offset) to
 * "YYYY-MM-DD HH:MM UTC" form. ALWAYS append the UTC marker — the
 * point of this helper is to never display naked time.
 *
 * Input:  "2026-05-17 02:33:11"  (sqlite datetime('now') format)
 * Output: "2026-05-17 02:33 UTC"
 *
 * Also accepts ISO-T form "2026-05-17T02:33:11Z".
 */
export function fmtUtcWithTz(utc: string | null | undefined): string {
  if (!utc) return ''
  const cleaned = utc.replace('T', ' ').replace(/Z$/, '')
  const m = cleaned.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/)
  if (!m) return utc
  return `${m[1]} ${m[2]} UTC`
}

/**
 * Primary helper: format a timestamp for display, preferring the
 * local-with-offset variant and falling back to the UTC variant
 * (annotated as UTC so the frame is never ambiguous).
 *
 * Usage:
 *   {fmtTime(row.local_created_at, row.created_at)}
 *
 * Always pass BOTH if the row has them — the function picks correctly.
 */
export function fmtTime(
  localIso?: string | null,
  utcIso?: string | null,
): string {
  if (localIso) return fmtLocalWithTz(localIso)
  if (utcIso) return fmtUtcWithTz(utcIso)
  return ''
}

/**
 * Relative time ("2 m ago", "3 h ago", "1 d ago") computed in the
 * viewer's frame from a UTC ISO string. Returns "" if unparseable.
 *
 * For display surfaces that want both relative and absolute, use:
 *   <span title={fmtTime(local, utc)}>{fmtRelative(utc)}</span>
 * so hover shows the exact local-with-tz time, click shows the
 * relative age.
 */
export function fmtRelative(utc: string | null | undefined): string {
  if (!utc) return ''
  const d = new Date(utc.includes('T') ? utc : utc.replace(' ', 'T') + 'Z')
  if (isNaN(d.getTime())) return ''
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.floor(months / 12)}y ago`
}
