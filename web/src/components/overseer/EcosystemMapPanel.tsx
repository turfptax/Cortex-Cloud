/**
 * Slice 10.4 Phase 1: Ecosystem visualizer (static map).
 *
 * Shows the overseer's tool/tick-step/hook surface as a React Flow
 * graph so Tory can orient to what exists before drilling into
 * per-run traces (Phase 2).
 *
 * Categories of nodes:
 *   - Hooks (5)        — boot / chat / journal_step / tick_scheduled / bell_action
 *   - Tick steps (16)  — Step 0..12 (incl. Slice 10's B-GC, C-grad, C-runs)
 *   - Tools (~36)      — every chat_tools.TOOL_DEFINITIONS entry
 *   - B agents (2)     — theme_check, project_merge_check
 *   - C agents (live)  — populated as graduations happen
 *
 * Edges:
 *   - Hook -> tool        : callable_from relation (dashed)
 *   - Tool -> B agent     : dispatch_b_<name> binds to B (solid)
 *   - Hook -> tick step   : tick_scheduled is the entry to all steps
 *   - Tick step -> tool   : journal_step (Step 5) calls tools
 *
 * Layout: column-grouped force layout. Hooks on left, tick steps
 * middle, tools right, B/C agents far right.
 *
 * Click any node to focus it; the detail panel on the right shows
 * description + relationships. Phase 2 will add a "View runs of
 * this surface" link from the detail panel.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  GraphCanvas,
  type EngineEdge,
  type EngineNode,
} from '../../lib/graphengine'
import { apiFetch } from '../../lib/api'

// ── Types matching /api/overseer/ecosystem ──────────────────────

interface EcoHook {
  id: string
  label: string
  description: string
}

interface EcoTickStep {
  id: number
  name: string
  label: string
  fires_when: string
  uses_llm: boolean
  description: string
}

interface EcoTool {
  name: string
  description: string
  category: string
  callable_from: string[]  // ["chat", "journal_step"]
}

interface EcoBAgent {
  name: string
  marker: string
  model: string
  max_tokens: number
  description: string
}

interface EcoCAgent {
  id: number
  name: string
  graduated_from_b_name: string
  status: string
  cadence_minutes: number
  last_run_at: string | null
}

interface EcosystemResp {
  ok: boolean
  generated_at: string
  hooks: EcoHook[]
  tick_steps: EcoTickStep[]
  tools: EcoTool[]
  b_agents: EcoBAgent[]
  c_agents: EcoCAgent[]
  counts: Record<string, number>
  error?: string
}

// ── Node payload (the data attached to each graph node) ─────────

type NodeKind = 'hook' | 'tick_step' | 'tool' | 'b_agent' | 'c_agent'

interface EcoNodeData {
  kind: NodeKind
  label: string
  sublabel?: string
  category?: string         // tool category drives color
  description?: string
  raw: EcoHook | EcoTickStep | EcoTool | EcoBAgent | EcoCAgent
}

// ── Color palette ───────────────────────────────────────────────

const KIND_FILL: Record<NodeKind, string> = {
  hook:      '#06b6d4',  // cyan — entry points
  tick_step: '#8b5cf6',  // violet — scheduled flow
  tool:      '#94a3b8',  // slate — read-by-default
  b_agent:   '#10b981',  // emerald — B audit agents
  c_agent:   '#f59e0b',  // amber — promoted, scheduled C agents
}

const TOOL_CATEGORY_FILL: Record<string, string> = {
  read:              '#94a3b8',  // slate
  write:             '#3b82f6',  // blue
  synthesis:         '#a78bfa',  // soft violet
  scan:              '#06b6d4',  // cyan
  b_agent_tool:      '#10b981',  // emerald (matches B)
  sibling_dispatch:  '#ef4444',  // red — costs Tory's budget
  chat_mgmt:         '#f59e0b',  // amber
  c_promotion:       '#fbbf24',  // bright amber
  other:             '#64748b',  // muted slate
}

const KIND_LABEL: Record<NodeKind, string> = {
  hook:      'Hook',
  tick_step: 'Step',
  tool:      'Tool',
  b_agent:   'B agent',
  c_agent:   'C agent',
}

// ── Component ───────────────────────────────────────────────────

export function EcosystemMapPanel() {
  const [eco, setEco] = useState<EcosystemResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [filter, setFilter] = useState<{
    hooks: boolean
    tick_steps: boolean
    tools: boolean
    b_agents: boolean
    c_agents: boolean
  }>({
    hooks: true,
    tick_steps: true,
    tools: true,
    b_agents: true,
    c_agents: true,
  })

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const j = await apiFetch<EcosystemResp>('/overseer/ecosystem')
      if (!j.ok) throw new Error(j.error || 'ecosystem load failed')
      setEco(j)
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // ── Build nodes + edges from the ecosystem response ────────────

  const { nodes, edges } = useMemo(() => {
    const ns: EngineNode<EcoNodeData>[] = []
    const es: EngineEdge<{ kind: string }>[] = []
    if (!eco) return { nodes: ns, edges: es }

    if (filter.hooks) {
      for (const h of eco.hooks) {
        ns.push({
          id: `hook:${h.id}`,
          data: {
            kind: 'hook',
            label: h.label,
            description: h.description,
            raw: h,
          },
        })
      }
    }

    if (filter.tick_steps) {
      for (const s of eco.tick_steps) {
        ns.push({
          id: `step:${s.id}`,
          data: {
            kind: 'tick_step',
            label: s.label,
            sublabel: s.fires_when,
            description: s.description,
            raw: s,
          },
        })
      }
      // tick_scheduled hook -> every step (entry edges)
      if (filter.hooks) {
        for (const s of eco.tick_steps) {
          es.push({
            source: 'hook:tick_scheduled',
            target: `step:${s.id}`,
            data: { kind: 'fires' },
          })
        }
      }
    }

    if (filter.tools) {
      for (const t of eco.tools) {
        ns.push({
          id: `tool:${t.name}`,
          data: {
            kind: 'tool',
            label: t.name,
            sublabel: t.category,
            category: t.category,
            description: t.description,
            raw: t,
          },
        })
        if (filter.hooks) {
          for (const cf of t.callable_from) {
            es.push({
              source: `hook:${cf}`,
              target: `tool:${t.name}`,
              data: { kind: 'callable' },
            })
          }
        }
        // journal_step is also a tick step (step #9); thread its
        // edge to step:9 as well for the tool→step relationship
        if (filter.tick_steps && t.callable_from.includes('journal_step')) {
          es.push({
            source: 'step:9',
            target: `tool:${t.name}`,
            data: { kind: 'step_calls' },
          })
        }
      }
    }

    if (filter.b_agents) {
      for (const b of eco.b_agents) {
        ns.push({
          id: `b:${b.name}`,
          data: {
            kind: 'b_agent',
            label: b.name,
            sublabel: b.marker,
            description: b.description,
            raw: b,
          },
        })
        // tool dispatch_b_<name> binds to this B
        const toolName = `dispatch_b_${b.name}`
        if (filter.tools && eco.tools.find((t) => t.name === toolName)) {
          es.push({
            source: `tool:${toolName}`,
            target: `b:${b.name}`,
            data: { kind: 'binds' },
          })
        }
      }
    }

    if (filter.c_agents) {
      for (const c of eco.c_agents) {
        ns.push({
          id: `c:${c.id}`,
          data: {
            kind: 'c_agent',
            label: c.name,
            sublabel: `from ${c.graduated_from_b_name}`,
            description: `${c.cadence_minutes}min cadence, ${c.status}`,
            raw: c,
          },
        })
        // C inherits from B parent
        if (filter.b_agents) {
          es.push({
            source: `b:${c.graduated_from_b_name}`,
            target: `c:${c.id}`,
            data: { kind: 'graduates' },
          })
        }
      }
    }

    return { nodes: ns, edges: es }
  }, [eco, filter])

  // ── Render functions for the graph canvas ───────────────────────

  const renderNode = useCallback(
    (node: EngineNode<EcoNodeData>, state: { active: boolean; dimmed: boolean }) => {
      const d = node.data
      let fill = KIND_FILL[d.kind]
      if (d.kind === 'tool' && d.category && TOOL_CATEGORY_FILL[d.category]) {
        fill = TOOL_CATEGORY_FILL[d.category]
      }
      const opacity = state.dimmed ? 0.3 : 1.0
      const ring = state.active ? '0 0 0 3px #f8fafc' : 'none'
      const w =
        d.kind === 'hook' || d.kind === 'tick_step'
          ? 180
          : d.kind === 'b_agent' || d.kind === 'c_agent'
          ? 170
          : 150
      const h = d.kind === 'hook' || d.kind === 'tick_step' ? 64 : 52
      return (
        <div
          style={{
            width: w,
            height: h,
            background: fill,
            opacity,
            boxShadow: ring,
            borderRadius: 10,
            padding: '8px 10px',
            color: '#0f172a',
            fontSize: 11,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            cursor: 'pointer',
          }}
          onClick={() => setActiveId(node.id)}
        >
          <div style={{ fontWeight: 600, lineHeight: 1.2 }}>{d.label}</div>
          {d.sublabel && (
            <div
              style={{
                fontSize: 10,
                opacity: 0.85,
                marginTop: 2,
                lineHeight: 1.2,
              }}
            >
              {d.sublabel}
            </div>
          )}
        </div>
      )
    },
    [],
  )

  const edgeStyle = useCallback(
    (
      edge: EngineEdge<{ kind: string }>,
      state: { highlighted: boolean; dimmed: boolean },
    ) => {
      const k = edge.data?.kind || ''
      const base: Record<string, { color: string; width: number; dash?: string }> = {
        callable:    { color: '#475569', width: 1, dash: '4 3' },
        step_calls:  { color: '#8b5cf6', width: 1, dash: '4 3' },
        fires:       { color: '#06b6d4', width: 1.5 },
        binds:       { color: '#10b981', width: 2 },
        graduates:   { color: '#f59e0b', width: 2 },
      }
      const cfg = base[k] || { color: '#64748b', width: 1 }
      return {
        stroke: cfg.color,
        strokeWidth: state.highlighted ? cfg.width + 1 : cfg.width,
        strokeDasharray: cfg.dash,
        opacity: state.dimmed ? 0.15 : state.highlighted ? 1 : 0.6,
      }
    },
    [],
  )

  const nodeSize = useCallback((node: EngineNode<EcoNodeData>) => {
    const d = node.data
    if (d.kind === 'hook' || d.kind === 'tick_step') return { w: 180, h: 64 }
    if (d.kind === 'b_agent' || d.kind === 'c_agent') return { w: 170, h: 52 }
    return { w: 150, h: 52 }
  }, [])

  const activeNode = useMemo(() => {
    if (!activeId) return null
    return nodes.find((n) => n.id === activeId) || null
  }, [activeId, nodes])

  // ── Render ──────────────────────────────────────────────────────

  if (loading && !eco) {
    return <div className="p-4 text-text-muted">Loading ecosystem map…</div>
  }
  if (error) {
    return (
      <div className="p-4">
        <div className="text-red-400 text-sm">{error}</div>
        <button
          onClick={refresh}
          className="mt-2 px-3 py-1 bg-surface-tertiary rounded text-xs"
        >
          Retry
        </button>
      </div>
    )
  }
  if (!eco) return null

  return (
    <div className="flex h-full" style={{ minHeight: 700 }}>
      {/* Main graph */}
      <div className="flex-1 relative">
        <GraphCanvas
          nodes={nodes}
          edges={edges}
          activeNodeId={activeId}
          renderNode={renderNode}
          edgeStyle={edgeStyle}
          nodeSize={nodeSize}
          layout={{
            kind: 'force',
            linkDistance: 140,
            linkStrength: 0.12,
            charge: -600,
            collidePadding: 12,
            alphaDecay: 0.025,
          }}
          background={{
            color: '#0f172a',
            dots: { gap: 24, size: 1, color: '#1e293b' },
          }}
          onNodeClick={(id) => setActiveId(id)}
          onPaneClick={() => setActiveId(null)}
        />

        {/* Filter strip */}
        <div className="absolute top-3 left-3 flex items-center gap-2 bg-surface-secondary/95 backdrop-blur rounded-lg px-3 py-2 text-xs">
          <span className="text-text-muted">Show:</span>
          {(Object.keys(filter) as Array<keyof typeof filter>).map((k) => (
            <label key={k} className="flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={filter[k]}
                onChange={(e) =>
                  setFilter({ ...filter, [k]: e.target.checked })
                }
              />
              <span className="capitalize">{k.replace('_', ' ')}</span>
            </label>
          ))}
          <button
            onClick={refresh}
            className="ml-2 px-2 py-0.5 bg-surface-tertiary hover:bg-surface-tertiary/70 rounded"
          >
            Refresh
          </button>
        </div>

        {/* Legend */}
        <div className="absolute bottom-3 left-3 bg-surface-secondary/95 backdrop-blur rounded-lg px-3 py-2 text-xs space-y-1">
          <div className="font-semibold text-text-secondary mb-1">Legend</div>
          {(Object.keys(KIND_FILL) as NodeKind[]).map((k) => (
            <div key={k} className="flex items-center gap-2">
              <div
                className="w-3 h-3 rounded"
                style={{ background: KIND_FILL[k] }}
              />
              <span>{KIND_LABEL[k]}</span>
            </div>
          ))}
        </div>

        {/* Counts */}
        <div className="absolute top-3 right-3 bg-surface-secondary/95 backdrop-blur rounded-lg px-3 py-2 text-xs">
          <div className="font-semibold text-text-secondary mb-1">Counts</div>
          {Object.entries(eco.counts).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-4">
              <span className="text-text-muted capitalize">
                {k.replace('_', ' ')}
              </span>
              <span className="font-mono">{v}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Detail panel */}
      <div
        className="border-l border-border-subtle bg-surface-secondary overflow-y-auto"
        style={{ width: 320 }}
      >
        {activeNode ? (
          <NodeDetail node={activeNode} />
        ) : (
          <div className="p-4 text-xs text-text-muted">
            <div className="font-semibold text-text-secondary mb-2 text-sm">
              Ecosystem map
            </div>
            <p className="leading-relaxed">
              Click any node to see its description, category, and the
              relationships it participates in.
            </p>
            <div className="mt-4 space-y-2">
              <div>
                <span className="text-text-primary">Hooks</span> — entry
                points where execution begins (boot, chat, journal step,
                scheduled tick, bell click).
              </div>
              <div>
                <span className="text-text-primary">Tick steps</span> — the
                16-step loop the scheduled tick runs every 15min. Step 5
                is the tool-enabled journal (Slice 9.9).
              </div>
              <div>
                <span className="text-text-primary">Tools</span> — every
                callable in <code>chat_tools.TOOL_DEFINITIONS</code>.
                Color = category.
              </div>
              <div>
                <span className="text-text-primary">B agents</span> —
                stateless audit specialists (Slice 10). Each has a
                corresponding <code>dispatch_b_*</code> tool that fires
                it.
              </div>
              <div>
                <span className="text-text-primary">C agents</span> — B
                patterns Tory promoted; run on a schedule.
              </div>
            </div>
            <div className="mt-4 pt-3 border-t border-border-subtle">
              <div className="text-text-muted">
                Per-run trace viewer (Phase 2) lands in the next slice. It
                shows what each actual tick / chat turn / B dispatch did
                with cost, latency, and a rating input.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Detail panel for a focused node ─────────────────────────────

function NodeDetail({ node }: { node: EngineNode<EcoNodeData> }) {
  const d = node.data
  const kindFill =
    d.kind === 'tool' && d.category && TOOL_CATEGORY_FILL[d.category]
      ? TOOL_CATEGORY_FILL[d.category]
      : KIND_FILL[d.kind]
  return (
    <div className="p-4 text-xs space-y-3">
      <div>
        <div
          className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
          style={{ background: kindFill, color: '#0f172a' }}
        >
          {KIND_LABEL[d.kind]}
          {d.category && ` · ${d.category}`}
        </div>
        <div className="font-semibold text-text-primary text-sm mt-2 break-words">
          {d.label}
        </div>
        {d.sublabel && (
          <div className="text-text-muted mt-1">{d.sublabel}</div>
        )}
      </div>
      {d.description && (
        <div>
          <div className="text-text-muted mb-1">Description</div>
          <div className="text-text-secondary leading-relaxed">
            {d.description}
          </div>
        </div>
      )}
      {/* Kind-specific surface */}
      {d.kind === 'tick_step' && (
        <TickStepDetails step={d.raw as EcoTickStep} />
      )}
      {d.kind === 'tool' && <ToolDetails tool={d.raw as EcoTool} />}
      {d.kind === 'b_agent' && <BAgentDetails b={d.raw as EcoBAgent} />}
      {d.kind === 'c_agent' && <CAgentDetails c={d.raw as EcoCAgent} />}
    </div>
  )
}

function TickStepDetails({ step }: { step: EcoTickStep }) {
  return (
    <div className="space-y-2">
      <KV k="Fires when" v={step.fires_when} />
      <KV k="Uses LLM" v={step.uses_llm ? 'yes' : 'no'} />
      <KV k="Loop function" v={`_${step.name}`} />
    </div>
  )
}

function ToolDetails({ tool }: { tool: EcoTool }) {
  return (
    <div className="space-y-2">
      <KV k="Category" v={tool.category} />
      <KV k="Callable from" v={tool.callable_from.join(', ')} />
    </div>
  )
}

function BAgentDetails({ b }: { b: EcoBAgent }) {
  return (
    <div className="space-y-2">
      <KV k="Marker" v={b.marker} />
      <KV k="Model" v={b.model} />
      <KV k="Max tokens" v={String(b.max_tokens)} />
      <KV k="Tool name" v={`dispatch_b_${b.name}`} />
    </div>
  )
}

function CAgentDetails({ c }: { c: EcoCAgent }) {
  return (
    <div className="space-y-2">
      <KV k="Status" v={c.status} />
      <KV k="Parent B" v={c.graduated_from_b_name} />
      <KV k="Cadence" v={`${c.cadence_minutes} min`} />
      <KV k="Last run" v={c.last_run_at || '(never)'} />
    </div>
  )
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <span className="text-text-muted shrink-0" style={{ minWidth: 90 }}>
        {k}
      </span>
      <span className="text-text-secondary break-words">{v}</span>
    </div>
  )
}
