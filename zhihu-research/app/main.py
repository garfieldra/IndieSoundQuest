"""Read-only adapter for Zhihu's official Open Platform search API.

The Access Secret stays only in this process' environment.  The public Agent
never receives the secret and can invoke only this narrow, validated endpoint.
"""
from __future__ import annotations

import os
import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="IndieSoundQuest Zhihu Research Sidecar", version="0.1.0")
QUERY_PURPOSES = {"candidate_discovery", "cultural_context", "recommendation_validation"}


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    purpose: str
    limit: int = Field(default=5, ge=1, le=8)


def _compact(value: object, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _url(value: object) -> str | None:
    url = str(value or "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and (host == "zhihu.com" or host.endswith(".zhihu.com")):
        return url
    return None


def _source_type(item: dict, url: str) -> str:
    kind = str(item.get("ContentType") or item.get("type") or item.get("content_type") or "").lower()
    if "article" in kind or "/p/" in url:
        return "ARTICLE"
    return "ANSWER"


def _project(items: object, purpose: str, limit: int) -> list[dict]:
    values = items.get("Items", items.get("items", [])) if isinstance(items, dict) else items
    if not isinstance(values, list):
        return []
    sources, seen = [], set()
    for item in values:
        if not isinstance(item, dict):
            continue
        url = _url(item.get("Url") or item.get("url") or item.get("link") or item.get("target_url"))
        title = _compact(item.get("Title") or item.get("title") or item.get("question_title") or item.get("name"), 180)
        snippet = _compact(item.get("ContentText") or item.get("excerpt") or item.get("content") or item.get("summary") or title, 280)
        if not url or not title or not snippet or url in seen:
            continue
        seen.add(url)
        author = item.get("AuthorName") or item.get("author") or item.get("author_name")
        if isinstance(author, dict):
            author = author.get("name")
        sources.append({
            "provider": "ZHIHU", "sourceType": _source_type(item, url), "sourceUrl": url,
            "title": title, "authorDisplayName": _compact(author, 80) or None,
            "snippet": snippet, "queryPurpose": purpose,
            "retrievedAt": datetime.now(timezone.utc).isoformat(), "contentConfidence": "LOW",
            "platformMetadata": {
                "contentKind": _source_type(item, url),
                "contentId": _compact(item.get("ContentID"), 80) or None,
                "authorityLevel": _compact(item.get("AuthorityLevel"), 12) or None,
            },
        })
        if len(sources) >= limit:
            break
    return sources


@app.get("/health")
async def health():
    return {"status": "UP", "configured": bool(os.getenv("ZHIHU_ACCESS_SECRET", "").strip()), "authMode": "OPEN_PLATFORM"}


@app.post("/v1/research/search")
async def search(request: SearchRequest):
    if request.purpose not in QUERY_PURPOSES:
        raise HTTPException(422, "invalid research purpose")
    secret = os.getenv("ZHIHU_ACCESS_SECRET", "").strip()
    if not secret:
        raise HTTPException(401, "PLATFORM_AUTH_REQUIRED")
    # Official search is read-only and idempotent.  A single bounded retry
    # smooths transient upstream/network failures without turning a platform
    # error into an unbounded polling loop.
    payload: dict | None = None
    cli_environment = os.environ.copy()
    # Preserve HOME/CA/proxy settings supplied by the container runtime.  The
    # official binary may use them for platform configuration and transport;
    # the only credential it receives remains the sidecar-only secret.
    cli_environment["ZHIHU_ACCESS_SECRET"] = secret
    for attempt in range(2):
        try:
            process = await asyncio.create_subprocess_exec(
                "zhihu-cli", "search", "zhihu", "--query", request.query,
                "--count", str(min(request.limit, 10)),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=cli_environment,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
            # The official CLI returns structured platform error JSON with a
            # non-zero exit code.  Preserve that classified error instead of
            # collapsing rate limits or invalid credentials into a 503.
            candidate = json.loads(stdout.decode("utf-8", errors="replace"))
            if isinstance(candidate, dict):
                payload = candidate
                break
        except (TimeoutError, OSError, json.JSONDecodeError):
            if 'process' in locals() and process.returncode is None:
                process.kill(); await process.communicate()
        if attempt == 0:
            await asyncio.sleep(0.35)
    if payload is None:
        raise HTTPException(503, "PLATFORM_UNAVAILABLE")
    code = payload.get("Code") if isinstance(payload, dict) else None
    if code in {20001, "20001"}:
        raise HTTPException(401, "PLATFORM_AUTH_REQUIRED")
    if code in {30001, "30001", 30002, "30002"}:
        raise HTTPException(429, "PLATFORM_RATE_LIMITED")
    if not isinstance(payload, dict) or code not in {0, "0"}:
        raise HTTPException(502, "PLATFORM_REJECTED_REQUEST")
    return {"items": _project(payload.get("Data", payload), request.purpose, request.limit)}
