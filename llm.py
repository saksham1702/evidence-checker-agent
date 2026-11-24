"""One function to call Groq with a strict JSON schema, plus a small disk cache.

The cache keys on the full request, so a re-run on unchanged pages costs no
tokens and gives the same answers. Delete .cache/ or pass --fresh to bypass it.
"""

import hashlib
import json
import os
from pathlib import Path

from groq import Groq

DEFAULT_MODEL = "openai/gpt-oss-120b"
CACHE_DIR = Path(__file__).parent / ".cache"
stats = {"api_calls": 0, "cache_hits": 0, "tokens": 0}
use_cache = True
_client = None


def model() -> str:
    return os.environ.get("CHECKER_MODEL", DEFAULT_MODEL)


def ask_json(system: str, user: str, schema_name: str, schema: dict) -> dict:
    """Send one chat request and return the parsed JSON object the model produced."""
    global _client
    body = {
        "model": model(),
        "temperature": 0,
        "seed": 7,
        "reasoning_effort": "medium",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
    }
    path = CACHE_DIR / (hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() + ".json")
    if use_cache and path.exists():
        stats["cache_hits"] += 1
        return json.loads(path.read_text())

    # max_retries covers 429s: the SDK waits for the retry-after header Groq sends
    _client = _client or Groq(max_retries=8, timeout=120)
    resp = _client.chat.completions.create(**body)
    stats["api_calls"] += 1
    stats["tokens"] += resp.usage.total_tokens if resp.usage else 0
    data = json.loads(resp.choices[0].message.content)
    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data))
    return data
