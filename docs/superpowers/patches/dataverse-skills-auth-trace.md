# Patch: Dataverse-skills `scripts/auth.py` — Optional Trace Hook

**Target repo:** `microsoft/Dataverse-skills`
**Target file:** `.github/plugins/dataverse/scripts/auth.py`
**Why:** SkillOpt's live-verification subsystem needs to observe HTTP calls the agent makes through the Dataverse SDK and record the GUIDs of records it creates. The cleanest place to do this is the SDK's existing auth wrapper, which already gates every HTTP request.

This patch is **optional** for response-only training — it's only needed for evals that use the `live` block to verify side effects against the real Dataverse env.

## Behavior

When the environment variable `DATAVERSE_TRACE_FILE` is set:
- Every HTTP request the SDK makes (via the session created in `get_client()`) appends one JSON line to that file: `{"ts": <epoch>, "method": "POST", "url": "...", "status": 200, "summary": {...}}`
- When a 2xx response comes back from a record-create or upsert endpoint, the response's GUID(s) are extracted and appended one-per-line to the sibling file `<DATAVERSE_TRACE_FILE without .jsonl>_guids.jsonl` (i.e., `dataverse_trace_guids.jsonl`).

When the env var is unset, behavior is unchanged — no overhead, no files written.

## The patch (concept)

In `get_client()`, after the `DataverseClient` is constructed but before it's returned, wrap (or attach a listener to) the underlying `requests.Session` so that:

1. After every `Session.send()` (or via the SDK's existing response hooks if it has any), if `os.environ.get("DATAVERSE_TRACE_FILE")` is non-empty, append a trace record to that path.
2. If the response's URL matches a create endpoint pattern (`/v9.2/<table>(<...>)`, `/CreateMultiple`, `/UpsertMultiple`) AND the status is 2xx, extract `OData-EntityId` header or response body GUIDs and append them to the `_guids.jsonl` sibling file.

## Reference implementation sketch

```python
import json
import os
import re
import time
import threading
from urllib.parse import urlparse

_TRACE_LOCK = threading.Lock()

# Match record-create/upsert endpoints: PATCH/POST on /v9.2/<entityset>(<guid>) or /CreateMultiple
_CREATE_URL_RE = re.compile(r"/v9\.2/([a-z][a-z0-9_]*)(\([^)]*\))?$|/CreateMultiple$|/UpsertMultiple$")
# OData-EntityId header format: <base>/v9.2/<entityset>(<guid>)
_GUID_HEADER_RE = re.compile(r"/v9\.2/([a-z][a-z0-9_]*)\(([0-9a-f-]{36})\)")


def _trace_path() -> str:
    return os.environ.get("DATAVERSE_TRACE_FILE", "")


def _guids_path() -> str:
    p = _trace_path()
    if not p:
        return ""
    if p.endswith(".jsonl"):
        return p[:-6] + "_guids.jsonl"
    return p + "_guids.jsonl"


def _record_trace(method: str, url: str, status: int, response_body_summary: dict | None = None) -> None:
    """Append one JSON line to the trace file. Safe under thread contention."""
    path = _trace_path()
    if not path:
        return
    rec = {
        "ts": time.time(),
        "method": method,
        "url": url,
        "status": status,
        "summary": response_body_summary or {},
    }
    with _TRACE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def _record_guids(table: str, guids: list[str]) -> None:
    """Append each created GUID to the sibling guids file."""
    path = _guids_path()
    if not path:
        return
    with _TRACE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            for g in guids:
                f.write(json.dumps({"table": table, "guid": g, "ts": time.time()}) + "\n")


def _hook_response(response):
    """`requests` response hook — called after every request the SDK makes."""
    if not _trace_path():
        return response
    try:
        url = response.url or ""
        status = response.status_code
        method = response.request.method
        _record_trace(method=method, url=url, status=status)
        # Extract GUIDs from create/upsert responses
        if 200 <= status < 300:
            # Single create: OData-EntityId header
            entity_id = response.headers.get("OData-EntityId", "")
            if entity_id:
                m = _GUID_HEADER_RE.search(entity_id)
                if m:
                    _record_guids(m.group(1), [m.group(2)])
            # CreateMultiple: response body has list of created records
            ctype = response.headers.get("Content-Type", "")
            if "application/json" in ctype and ("CreateMultiple" in url or "UpsertMultiple" in url):
                try:
                    body = response.json()
                    items = body.get("value") or body.get("Targets") or []
                    table_match = re.search(r"/v9\.2/([a-z][a-z0-9_]*)", url)
                    table = table_match.group(1) if table_match else "unknown"
                    guids = []
                    for item in items if isinstance(items, list) else []:
                        # Each item should have the entity's primary id
                        for k, v in item.items():
                            if k.endswith("id") and isinstance(v, str) and len(v) == 36:
                                guids.append(v)
                                break
                    if guids:
                        _record_guids(table, guids)
                except Exception:
                    pass
    except Exception:
        # Trace hook must never break the actual SDK call.
        pass
    return response


# In get_client():
def get_client(skill: str):
    # ... existing setup ...
    client = DataverseClient(...)
    # Attach the trace hook to the SDK's underlying session
    session = client._session if hasattr(client, "_session") else client.session
    if session is not None:
        existing = session.hooks.get("response", [])
        if isinstance(existing, list):
            existing.append(_hook_response)
        else:
            existing = [existing, _hook_response]
        session.hooks["response"] = existing
    return client
```

## How to apply

1. Open `microsoft/Dataverse-skills` clone at `.github/plugins/dataverse/scripts/auth.py`.
2. Find the existing `get_client()` function — locate where the `DataverseClient` is constructed and returned.
3. Add the helper functions (`_trace_path`, `_guids_path`, `_record_trace`, `_record_guids`, `_hook_response`) near the top of the file or in a small private section.
4. Attach `_hook_response` to the client's underlying `requests.Session` via `session.hooks["response"]`. The SDK's exact attribute name for the session may vary (`client._session`, `client.session`, or behind a property) — `dir(client)` to find it.
5. Test locally: `DATAVERSE_TRACE_FILE=/tmp/trace.jsonl python -c "from auth import get_client; c = get_client('test'); list(c.records.get('account', select=['name']))"` and verify `/tmp/trace.jsonl` populates.
6. Commit and PR into `microsoft/Dataverse-skills`. Title: `feat(auth): optional HTTP trace hook for SkillOpt eval verification`.

## Compatibility notes

- The hook is a NO-OP when `DATAVERSE_TRACE_FILE` is unset, so this is safe for normal users.
- The hook is best-effort: any exception inside it is swallowed so the SDK's real behavior is never broken.
- Thread-safety: uses a module-level lock around file writes. Worker pools (e.g., from `ThreadPoolExecutor`) are safe.
- The GUID-extraction heuristics may need tweaking for specific SDK response shapes — verify against your env on first use.
