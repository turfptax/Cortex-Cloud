"""Resolve a pasted YouTube URL/handle to a loop_youtube_channels entry.

The settings UI lets the owner paste whatever they have on the clipboard
(a channel link, an @handle, a video link, or a bare UC id) instead of
hand-crafting the `persona:channel_id[:project_tag]` line the ingester
wants.

Hard-won rule (2026-06-11, persona ingest): on a CHANNEL page the first
`"channelId"` in the HTML often belongs to a featured or related channel,
NOT the channel itself; `"externalId"` is the channel's own id. On a WATCH
page the uploader's id lives inside the `"videoDetails"` block. Either
way the result is only trusted after the RSS feed for that id answers,
because the feed is exactly what the ingester will poll, and its <title>
gives the canonical channel name for free.

Network access is injected (`fetch_text`) so tests run offline.
"""

from __future__ import annotations

import html as html_mod
import re
import urllib.parse
import urllib.request

_UC_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_EXTERNAL_ID = re.compile(r'"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"')
_CHANNEL_ID = re.compile(r'"channelId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"')
_FEED_TITLE = re.compile(r"<title>([^<]*)</title>")

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"


def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        # A browser-ish UA: YouTube serves the full initial-data payload
        # to browsers and a stub to unknown agents.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept-Language": "en",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _slug(text: str) -> str:
    """Persona slug: lowercase alphanumerics, at most 30 chars."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())[:30] or "channel"


def _page_url(raw: str) -> tuple[str | None, str]:
    """Map the pasted text to (page_url_to_fetch, kind).

    kind: 'id' (no fetch needed; page_url carries the id),
          'channel' (channel-shaped page: use externalId),
          'watch' (video page: use videoDetails channelId).
    """
    s = (raw or "").strip()
    if not s:
        return None, ""
    if _UC_ID.match(s):
        return s, "id"
    if s.startswith("@"):
        return "https://www.youtube.com/" + s, "channel"
    if "://" not in s:
        s = "https://" + s
    try:
        parts = urllib.parse.urlparse(s)
    except ValueError:
        return None, ""
    host = (parts.netloc or "").lower()
    if not host.endswith(("youtube.com", "youtu.be")):
        return None, ""
    path = parts.path or "/"
    if host.endswith("youtu.be"):
        return "https://www.youtube.com/watch?v=" + path.strip("/"), "watch"
    m = re.match(r"^/channel/(UC[0-9A-Za-z_-]{22})", path)
    if m:
        return m.group(1), "id"
    if path.startswith(("/watch", "/shorts/", "/live/")):
        return "https://www.youtube.com" + path + (
            "?" + parts.query if parts.query else ""), "watch"
    if path.startswith(("/@", "/c/", "/user/")):
        return "https://www.youtube.com" + path.split("/featured")[0], "channel"
    return None, ""


def _extract_channel_id(page_html: str, kind: str) -> str | None:
    if kind == "channel":
        m = _EXTERNAL_ID.search(page_html)
        return m.group(1) if m else None
    # Watch page: scope to the videoDetails block so a related-channel id
    # earlier in the document cannot win.
    idx = page_html.find('"videoDetails"')
    if idx >= 0:
        m = _CHANNEL_ID.search(page_html, idx)
        if m:
            return m.group(1)
    # Degenerate watch page (or consent stub): externalId fallback.
    m = _EXTERNAL_ID.search(page_html)
    return m.group(1) if m else None


def resolve(raw: str, *, fetch_text=None) -> dict:
    """Resolve pasted text to a verified channel entry.

    Returns {ok, channel_id, title, persona, entry} or {ok: False, error}.
    Every success has passed the RSS check: the returned id is one the
    ingester's poll will actually answer for.
    """
    fetch = fetch_text or _default_fetch
    page, kind = _page_url(raw)
    if page is None:
        return {"ok": False, "error": (
            "could not read that as a YouTube link. Paste a channel URL "
            "(youtube.com/@handle or /channel/UC...), a video URL, or a "
            "bare UC... channel id.")}

    if kind == "id":
        channel_id = page
    else:
        try:
            page_html = fetch(page)
        except Exception as e:
            return {"ok": False,
                    "error": "could not fetch the page: {}".format(e)}
        channel_id = _extract_channel_id(page_html, kind)
        if not channel_id:
            return {"ok": False, "error": (
                "no channel id found on that page. If YouTube served a "
                "consent page, paste the /channel/UC... URL or the bare "
                "UC... id instead.")}

    # Trust nothing until the feed the ingester polls answers for it.
    try:
        feed = fetch(RSS_URL.format(channel_id))
    except Exception as e:
        return {"ok": False, "error": (
            "channel id {} did not verify against the RSS feed: {}".format(
                channel_id, e))}
    m = _FEED_TITLE.search(feed)
    title = html_mod.unescape(m.group(1).strip()) if m else ""

    # Prefer the @handle for the persona slug; fall back to the title.
    handle = None
    hm = re.search(r"@([A-Za-z0-9._-]+)", raw or "")
    if hm:
        handle = hm.group(1)
    persona = _slug(handle or title)
    return {
        "ok": True,
        "channel_id": channel_id,
        "title": title,
        "persona": persona,
        "entry": "{}:{}".format(persona, channel_id),
    }
