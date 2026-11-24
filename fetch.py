"""Load each entry of pages.json into an evidence source with a load status.

A source is a live URL or a saved snapshot file. Nothing else is fetched:
no link following, no crawling.
"""

import hashlib
import urllib.error
import urllib.request
from pathlib import Path

import trafilatura

USER_AGENT = "Mozilla/5.0 (compatible; EvidenceChecker/1.0; take-home research script)"
BLOCKED_HTTP = {401, 402, 403, 407, 429, 451}  # the server refused us, the page itself may be fine
TIMEOUT_S = 20
MIN_CHARS = 200    # less readable text than this counts as an empty page
MAX_CHARS = 8000   # keeps every LLM call under the Groq free tier's 8k tokens/minute


def load_source(ev_id: str, entry: dict, root: Path) -> dict:
    """Return {id, source, note, status, http_status, detail, chars, truncated, sha256, text}.

    status is one of ok, blocked, error, empty. Never raises.
    """
    source = entry["source"]
    item = {"id": ev_id, "source": source, "note": entry.get("note", ""), "status": "ok",
            "http_status": None, "detail": "", "chars": 0, "truncated": False, "sha256": None, "text": ""}
    try:
        if source.startswith(("http://", "https://")):
            html, item["http_status"] = _download(source)
            text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
        else:
            text = (root / source).read_text(encoding="utf-8")
    except urllib.error.HTTPError as e:
        item["http_status"] = e.code
        item["status"] = "blocked" if e.code in BLOCKED_HTTP else "error"
        item["detail"] = f"HTTP {e.code} {e.reason}"
        return item
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, ValueError) as e:
        item["status"], item["detail"] = "error", f"{type(e).__name__}: {e}"
        return item

    text = text.strip()
    if len(text) < MIN_CHARS:
        item["status"] = "empty"
        item["detail"] = f"only {len(text)} characters of readable text (paywall, script-only page or empty file)"
    elif len(text) > MAX_CHARS:
        cut = text.rfind(" ", 0, MAX_CHARS)
        text = text[: cut if cut > 0 else MAX_CHARS]
        item["truncated"] = True
        item["detail"] = f"text cut to the first {len(text)} characters"
    item["text"] = text
    item["chars"] = len(text)
    item["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return item


def _download(url: str) -> tuple[str, int]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace"), resp.status
