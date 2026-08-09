"""The pasted-link resolver finds the RIGHT channel id, offline.

Added 2026-08-09 for the settings UI's paste-a-link affordance. What has
to hold, per the 2026-06-11 persona-ingest lesson:

  - a channel page resolves via "externalId", NEVER the first
    "channelId" (that one is routinely a featured/related channel)
  - a watch page resolves via the channelId inside "videoDetails",
    not one that appears earlier in the document
  - /channel/UC... URLs and bare UC ids skip the page fetch entirely
  - every success is verified against the RSS feed the ingester polls,
    and the feed title becomes the canonical channel name
  - non-YouTube input is refused with a teaching message

Run: python scripts/test_youtube_resolver.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "overseer"))

from youtube_resolve import resolve, _page_url  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


GOOD_ID = "UCqUkg2M11LXsoSusLoh20YA"
DECOY_ID = "UCdecoydecoydecoydecoy12"

# A channel page whose FIRST "channelId" is a featured channel (the trap),
# with the real id only in "externalId".
CHANNEL_HTML = (
    '<html>{"header":{"featured":{"channelId":"' + DECOY_ID + '"}}}'
    'more stuff {"externalId":"' + GOOD_ID + '"} tail</html>'
)

# A watch page with a related-channel id BEFORE the videoDetails block.
WATCH_HTML = (
    '<html>{"related":{"channelId":"' + DECOY_ID + '"}}'
    '{"videoDetails":{"videoId":"abc","channelId":"' + GOOD_ID + '"}}</html>'
)

FEED_XML = (
    "<?xml version='1.0'?><feed><title>TURFPTAx</title>"
    "<entry><title>a video</title></entry></feed>"
)


def fake_fetch(url):
    if "feeds/videos.xml" in url:
        if GOOD_ID in url:
            return FEED_XML
        raise RuntimeError("HTTP 404")
    if "/watch" in url:
        return WATCH_HTML
    return CHANNEL_HTML


def main():
    print("input mapping:")
    check("bare id skips fetch", _page_url(GOOD_ID) == (GOOD_ID, "id"))
    check("/channel/ url skips fetch",
          _page_url(f"https://www.youtube.com/channel/{GOOD_ID}")
          == (GOOD_ID, "id"))
    check("@handle maps to channel page",
          _page_url("@TURFPTAx")
          == ("https://www.youtube.com/@TURFPTAx", "channel"))
    check("handle url maps to channel page",
          _page_url("https://youtube.com/@TURFPTAx")[1] == "channel")
    check("watch url maps to watch",
          _page_url("https://www.youtube.com/watch?v=abc123")[1] == "watch")
    check("youtu.be maps to watch",
          _page_url("https://youtu.be/abc123")[1] == "watch")
    check("non-youtube refused", _page_url("https://example.com/x")[0] is None)

    print("the externalId trap:")
    out = resolve("https://www.youtube.com/@TURFPTAx", fetch_text=fake_fetch)
    check("channel page uses externalId not first channelId",
          out.get("channel_id") == GOOD_ID, str(out))
    check("feed title becomes the name", out.get("title") == "TURFPTAx")
    check("persona slug from handle", out.get("persona") == "turfptax")
    check("entry shape", out.get("entry") == f"turfptax:{GOOD_ID}")

    print("watch pages:")
    out = resolve("https://www.youtube.com/watch?v=abc123",
                  fetch_text=fake_fetch)
    check("videoDetails id wins over earlier related id",
          out.get("channel_id") == GOOD_ID, str(out))
    check("persona falls back to feed title slug",
          out.get("persona") == "turfptax")

    print("verification gate:")
    out = resolve(DECOY_ID, fetch_text=fake_fetch)
    check("id that fails RSS check is refused",
          out.get("ok") is False and "verify" in out.get("error", ""))
    out = resolve("https://example.com/nope", fetch_text=fake_fetch)
    check("non-youtube input teaches the accepted forms",
          out.get("ok") is False and "channel URL" in out.get("error", ""))
    out = resolve("", fetch_text=fake_fetch)
    check("empty input refused", out.get("ok") is False)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
