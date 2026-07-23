"""Standalone copy-context page for non-MCP AIs (cloud port, Phase A).

Ported verbatim from the desktop Hub (hub/backend/routers/intro.py):
one URL, one big button, fetches the context brief from the same-origin
/api/overseer/intro proxy (served here by rest/hub_api.py) and copies
it to the clipboard. Served only in web-UI mode and Entra-gated via
_HUMAN_PATHS in app.py, so the browser session is what authorizes the
underlying corpus fetch.

Lives outside /api/* so the URL is short and memorable:
    https://<your-cortex-domain>/intro
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..config import get_settings


router = APIRouter()


_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport"
        content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Cortex Context - Copy for AI</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #0d0f12;
      --panel: #161a20;
      --fg: #e6e8eb;
      --muted: #9aa3ad;
      --accent: #7cdfd1;
      --accent-strong: #25b0a1;
      --warn: #d9a64a;
      --error: #d96363;
      --border: #232830;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f6f7f8;
        --panel: #ffffff;
        --fg: #16191d;
        --muted: #5b6470;
        --accent: #14857a;
        --accent-strong: #0e6b62;
        --warn: #b8771a;
        --error: #c63d3d;
        --border: #dde1e6;
      }
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--fg);
      font: 15px/1.45 -apple-system, BlinkMacSystemFont,
            "Segoe UI", Roboto, sans-serif;
    }
    .wrap {
      max-width: 880px;
      margin: 0 auto;
      padding: 24px 20px 80px;
    }
    h1 {
      font-size: 22px;
      margin: 0 0 6px;
      letter-spacing: -0.01em;
    }
    .sub {
      color: var(--muted);
      font-size: 13px;
      margin: 0 0 22px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      position: sticky;
      top: 0;
      background: var(--bg);
      padding: 14px 0;
      margin: -14px 0 14px;
      z-index: 5;
      border-bottom: 1px solid var(--border);
    }
    button {
      background: var(--accent);
      color: #000;
      border: 0;
      padding: 12px 18px;
      font-size: 15px;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      letter-spacing: 0.01em;
    }
    button:hover { background: var(--accent-strong); color: #fff; }
    button.secondary {
      background: transparent;
      color: var(--fg);
      border: 1px solid var(--border);
      font-weight: 500;
    }
    button.secondary:hover {
      background: var(--panel);
      border-color: var(--accent);
    }
    button:disabled {
      opacity: 0.4;
      cursor: progress;
    }
    .status {
      color: var(--muted);
      font-size: 13px;
      min-width: 0;
      flex: 1;
    }
    .status.ok { color: var(--accent); }
    .status.warn { color: var(--warn); }
    .status.err { color: var(--error); }
    .brief {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 22px;
      white-space: pre-wrap;
      word-break: break-word;
      font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo,
            Consolas, monospace;
      max-height: 60vh;
      overflow: auto;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 14px;
    }
    .meta code {
      background: var(--panel);
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid var(--border);
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Cortex Context - for AI paste</h1>
    <p class="sub">
      The 30-second brief about __OWNER_NAME__. Hit
      <strong>Copy</strong>, then paste into any AI chat that
      doesn't have the Cortex MCP loaded (Grok, ChatGPT browser, a
      phone, etc.).
    </p>

    <div class="actions">
      <button id="copy">📋  Copy brief</button>
      <button id="refresh" class="secondary">↻  Refresh</button>
      <button id="view-toggle" class="secondary">
        View raw markdown
      </button>
      <span id="status" class="status">loading…</span>
    </div>

    <div id="brief" class="brief">Loading the brief…</div>

    <p class="meta">
      Source: <code>GET /api/overseer/intro?format=markdown</code> ·
      this page lives at <code>/intro</code>.
    </p>
  </div>

  <script>
    const briefEl = document.getElementById("brief");
    const statusEl = document.getElementById("status");
    const copyBtn = document.getElementById("copy");
    const refreshBtn = document.getElementById("refresh");
    const viewToggleBtn = document.getElementById("view-toggle");

    let markdownText = "";
    let showingRaw = true;

    function setStatus(msg, cls) {
      statusEl.textContent = msg;
      statusEl.className = "status" + (cls ? " " + cls : "");
    }

    async function loadBrief() {
      setStatus("loading the brief…");
      copyBtn.disabled = true;
      try {
        const r = await fetch(
          "/api/overseer/intro?format=markdown",
          { headers: { "Accept": "application/json" } },
        );
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        if (!data.ok) {
          throw new Error(data.error || "unknown error");
        }
        markdownText = data.markdown || "";
        if (!markdownText) {
          throw new Error("brief is empty - overseer returned no markdown");
        }
        briefEl.textContent = markdownText;
        copyBtn.disabled = false;
        const lines = markdownText.split("\n").length;
        const chars = markdownText.length;
        setStatus(
          "ready · " + chars.toLocaleString() + " chars, " +
          lines.toLocaleString() + " lines",
          "ok",
        );
      } catch (e) {
        briefEl.textContent =
          "Failed to load the brief.\n\n" + e.message +
          "\n\nIs the Hub backend connected to the Cortex core " +
          "(Settings > Cortex Cloud)?";
        setStatus("error - see panel", "err");
        copyBtn.disabled = true;
      }
    }

    async function copyToClipboard() {
      if (!markdownText) return;
      try {
        await navigator.clipboard.writeText(markdownText);
        setStatus("copied ✓  paste into the AI now", "ok");
        copyBtn.textContent = "✓  Copied";
        setTimeout(() => {
          copyBtn.textContent = "📋  Copy brief";
        }, 2200);
      } catch (e) {
        // Fallback: select + execCommand (older Safari, some
        // restricted contexts)
        try {
          const ta = document.createElement("textarea");
          ta.value = markdownText;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.focus(); ta.select();
          const ok = document.execCommand("copy");
          document.body.removeChild(ta);
          if (ok) {
            setStatus(
              "copied ✓  (used fallback)  · paste into the AI",
              "ok",
            );
          } else {
            throw new Error("execCommand returned false");
          }
        } catch (e2) {
          setStatus(
            "couldn't copy automatically - select the brief and "
            + "copy manually",
            "warn",
          );
        }
      }
    }

    copyBtn.addEventListener("click", copyToClipboard);
    refreshBtn.addEventListener("click", loadBrief);
    viewToggleBtn.addEventListener("click", () => {
      // No-op for now - markdown is the only format. Button is here
      // so if we add a "rendered HTML" view later, the surface
      // exists. Hide it to keep the UI clean.
    });
    viewToggleBtn.style.display = "none";

    loadBrief();
  </script>
</body>
</html>
"""


@router.get("/intro", response_class=HTMLResponse)
async def intro_page():
    """The standalone copy-context page the owner pastes into non-MCP AIs."""
    html = _PAGE_HTML.replace("__OWNER_NAME__", get_settings().owner_name)
    return HTMLResponse(content=html)
