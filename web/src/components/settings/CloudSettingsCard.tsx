/** Settings, cloud edition.
 *
 * The desktop Settings page configures a LOCAL install: the Pi/core
 * connection, the MCP setup, the auto-updater, plugins, Lemon egress,
 * debug logs. None of that applies when the Hub is served from the
 * cloud gateway: there is no local config to edit, the corpus is the
 * gateway's own, and auth is the Entra session. This card replaces the
 * whole desktop Settings body in cloud mode with a read-only status
 * summary and a sign-out link. */
export function CloudSettingsCard() {
  return (
    <div className="space-y-6">
      <div className="bg-surface-secondary border border-border rounded-xl p-5">
        <h2 className="text-base font-semibold text-text-primary mb-3">
          ☁️ Cortex Cloud
        </h2>
        <dl className="text-sm space-y-2">
          <div className="flex justify-between">
            <dt className="text-text-muted">Corpus</dt>
            <dd className="text-text-secondary">cortex.turfptax.com</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-text-muted">Access</dt>
            <dd className="text-text-secondary">Microsoft (Entra) sign-in</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-text-muted">Config</dt>
            <dd className="text-text-secondary">
              Managed by the gateway — no local settings
            </dd>
          </div>
        </dl>
        <a
          href="/.auth/logout"
          className="inline-block mt-4 px-4 py-2 bg-surface-tertiary text-text-primary text-sm rounded-lg hover:bg-surface-hover transition-colors"
        >
          Sign out
        </a>
      </div>

      <div className="bg-surface-secondary border border-border rounded-xl p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-2">
          On your desktop
        </h3>
        <p className="text-sm text-text-secondary">
          Local features — importing Claude Code sessions, the Local LM
          chat, video capture, Lemon dispatch, and MCP setup — live in
          the Cortex desktop app. This web Hub is the window onto your
          cloud corpus.
        </p>
      </div>
    </div>
  )
}
