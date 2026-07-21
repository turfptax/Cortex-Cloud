"""Cortex Gateway - the single authenticated, internet-reachable service
fronting the canonical Cortex corpus.

Serves two audiences over one FastAPI process:
  - the phone app (React Native + Expo) via REST under /v1
  - external AI connectors (ChatGPT / Grok / Claude) via Streamable-HTTP MCP at /mcp

See DESIGN.md for the locked Phase 0 contract.
"""

__version__ = "0.1.0"
