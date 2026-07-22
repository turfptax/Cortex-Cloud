# Vision

Cortex is a portable memory that speaks MCP. The point is not another
notes app; it is a memory you own that every AI provider can connect to,
so your data and your hard-won lessons travel with you instead of being
trapped in one chat product.

```
   any AI provider  <-->  MCP  <-->  your Cortex
                            |
      exposes ──►  Memory · Projects · Rules · Skills
                   (People: owner-only, not over MCP; Plans: not yet exposed)
                            |
      kept alive by ─►  Overseer (curates)   Lemon Squeezer (learns)   Simples (plans)
```

## The five pillars

Cortex organizes what it remembers into five things. The design goal is
for every one of them to be reachable both in the web Hub and over MCP,
with People the deliberate standing exception (owner-only, not exposed
over MCP).

- **Memory**: the corpus, layered from summaries down to raw source, and
  searchable by meaning and by keyword.
- **Projects**: what you are working on, rolled up over time.
- **People**: your contacts and the interactions with them.
- **Rules**: the hard-won defaults you want every AI to respect.
- **Skills**: a living record of how you do things.

## The three engines

- **Overseer** curates in the background: it tidies memory, writes
  narratives and journals, and keeps the pillars coherent.
- **Lemon Squeezer** learns: it turns your interactions and feedback into
  durable lessons and promotes them into Rules and Skills.
- **Simples** plans: it turns goals into liquid time blocks that reflow
  around real events.

The through-line: every interaction leads to a squeezed lesson, your Rules
and Skills grow, the Overseer keeps the pillars tidy, and all of it stays
portable over MCP to whatever AI you reach for next. A tool that grows
with you.

## Roadmap

The organizing principle is that the MCP surface is the product. Work is
ordered by how directly it makes the pillars reachable from any AI.

1. **Pillars as MCP tools.** SHIPPED: Memory, Projects, Rules, and Skills
   are first-class MCP tools today, with read and write for an approved
   connection. People is the deliberate exception, kept owner-only and off
   MCP.
2. **The lessons loop.** Lemon Squeezer runs in the background, turning
   every interaction into Rules and Skills served to every AI via
   `/intro`.
3. **A leaner curator.** The Overseer focuses on keeping the pillars
   coherent, and the engine sheds anything that does not serve them.
4. **Planning everywhere.** Simples plans become corpus-native and
   MCP-exposed, reachable from any surface.
5. **A pillar-shaped Hub.** The web Hub's navigation becomes the five
   pillars, with the engines humming underneath.

Contributions and ideas toward this are welcome.
