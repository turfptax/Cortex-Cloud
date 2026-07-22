import {
  STATUS_DISPLAY,
  frameUrl,
  type SceneEntry,
  type VideoSession,
} from '../../lib/videoApi'

/** Shared rendering for an in-flight or completed VideoSession.
 *
 * Used by both FileMode (URL-driven) and JournalMode (upload-driven) —
 * the two modes converge after submit on the same polling loop and the
 * same display surface. */
export function SessionStatusView({ session }: { session: VideoSession }) {
  const display = STATUS_DISPLAY[session.status]
  const totalScenes = (session.progress?.total_scenes as number) ?? 0
  const currentScene = (session.progress?.current_scene as number) ?? 0
  const progressPct =
    totalScenes > 0 ? Math.min(100, (currentScene / totalScenes) * 100) : null

  return (
    <div className="space-y-4">
      {/* Status header */}
      <div className="bg-surface rounded-xl p-4 border border-border">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={`text-xs font-semibold px-2 py-0.5 rounded ${display.color}`}
              >
                {display.label}
              </span>
              <span className="text-xs text-text-muted font-mono">
                {session.id.slice(0, 8)}
              </span>
              {session.pushed_to_overseer && (
                <span className="text-[10px] uppercase tracking-wide text-success">
                  pushed
                </span>
              )}
            </div>
            <p className="text-xs text-text-muted mt-1 truncate">
              {summarizeSource(session.source)}
            </p>
          </div>
          <SceneCounter
            scenes={session.scenes.length}
            total={totalScenes || null}
          />
        </div>

        {progressPct !== null && session.status !== 'complete' && (
          <div className="mt-3 h-1.5 bg-surface-tertiary rounded-full overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        )}

        {session.error && (
          <p className="mt-3 text-xs text-error whitespace-pre-wrap">
            {session.error}
          </p>
        )}
      </div>

      {/* Scenes */}
      {session.scenes.length > 0 && (
        <div className="bg-surface rounded-xl p-4 border border-border space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Scenes
          </h3>
          <div className="space-y-3">
            {session.scenes.map((scene) => (
              <SceneCard
                key={scene.index}
                scene={scene}
                sessionId={session.id}
              />
            ))}
          </div>
        </div>
      )}

      {/* Narrative */}
      {session.narrative && (
        <div className="bg-surface rounded-xl p-5 border border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">
            Narrative
          </h3>
          <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
            {session.narrative}
          </p>
        </div>
      )}
    </div>
  )
}

function SceneCounter({
  scenes,
  total,
}: {
  scenes: number
  total: number | null
}) {
  return (
    <div className="text-right shrink-0">
      <div className="text-sm font-semibold text-text-primary">
        {scenes}
        {total ? ` / ${total}` : ''}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-text-muted">
        scenes
      </div>
    </div>
  )
}

function SceneCard({
  scene,
  sessionId,
}: {
  scene: SceneEntry
  sessionId: string
}) {
  return (
    <div className="bg-surface-secondary rounded-lg p-3 border border-border">
      <div className="flex gap-3">
        {scene.keyframe_paths.length > 0 && (
          <img
            src={frameUrl(sessionId, scene.index, 0)}
            alt={`Scene ${scene.index}`}
            className="w-32 h-20 object-cover rounded shrink-0 bg-surface-tertiary"
            loading="lazy"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-text-muted">
              #{scene.index}
            </span>
            <span className="text-xs text-text-muted">
              {fmtTime(scene.start_s)}–{fmtTime(scene.end_s)}
            </span>
            {scene.describer_model && (
              <span className="text-[10px] text-text-muted opacity-60 font-mono">
                {scene.describer_model}
              </span>
            )}
          </div>
          {scene.description ? (
            <p className="text-sm text-text-secondary mt-1 leading-snug">
              {scene.description}
            </p>
          ) : (
            <p className="text-xs text-text-muted italic mt-1">
              awaiting description…
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function summarizeSource(source: Record<string, unknown>): string {
  if (typeof source.url === 'string') return source.url
  if (typeof source.filename === 'string') return source.filename
  return JSON.stringify(source)
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60)
  const r = Math.floor(s % 60)
  return `${m}:${r.toString().padStart(2, '0')}`
}
