import { CloudSettingsCard } from './CloudSettingsCard'
import { ConnectionsCard } from './ConnectionsCard'
import { OverseerSettingsCard } from './OverseerSettingsCard'

/** Cloud Settings. The desktop cards (Pi connection, LM Studio, plugins,
 * Lemon sync, updater, debug logs, MCP-to-Pi setup) are gone; the cloud
 * app has no local hardware to configure. What remains: the overseer's
 * own dials (model, budgets, ingest), the cloud status, and the
 * connector approvals, which are the product's front door. */
export function SettingsPage() {
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <header className="px-6 py-4 border-b border-border shrink-0">
        <h1 className="text-xl font-bold text-text-primary">Settings</h1>
        <p className="text-xs text-text-muted mt-0.5">Cortex Cloud</p>
      </header>

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <OverseerSettingsCard />
        <CloudSettingsCard />
        <ConnectionsCard />
      </div>
    </div>
  )
}
