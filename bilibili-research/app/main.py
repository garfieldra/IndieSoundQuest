"""Read-only, bounded Bilibili public-video search adapter.

No account, cookies, comments, danmaku, media download, or interaction APIs.
"""
from __future__ import annotations
import asyncio
import html
import re
from datetime import datetime, timezone
from urllib.parse import quote
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="IndieSoundQuest Bilibili Research Sidecar", version="0.1.0")
PURPOSES = {"candidate_discovery", "cultural_context", "recommendation_validation"}

class SearchRequest(BaseModel):
    query: str = Field(min_length=4, max_length=240)
    purpose: str
    limit: int = Field(default=5, ge=1, le=8)

def text(value: object, limit: int) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))).split())[:limit]

@app.get("/health")
async def health(): return {"status": "UP", "authenticated": False, "mode": "public-read-only"}

@app.post("/v1/research/search")
async def search(request: SearchRequest):
    if request.purpose not in PURPOSES: raise HTTPException(422, "invalid research purpose")
    items = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36", "Referer": "https://www.bilibili.com/"}) as client:
                response = await client.get("https://api.bilibili.com/x/web-interface/search/type", params={"search_type": "video", "keyword": request.query, "page": 1})
                response.raise_for_status()
                items = response.json().get("data", {}).get("result", [])
                break
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            if attempt < 2: await asyncio.sleep(0.4 * (attempt + 1))
    if items is None: raise HTTPException(503, "PLATFORM_UNAVAILABLE")
    output = []
    for item in items:
        bvid = str(item.get("bvid") or "")
        title, description = text(item.get("title"), 180), text(item.get("description"), 280)
        if not bvid or not title: continue
        output.append({"provider":"BILIBILI","sourceType":"VIDEO","sourceUrl":f"https://www.bilibili.com/video/{quote(bvid, safe='')}","title":title,"authorDisplayName":text(item.get("author"),80) or None,"snippet":description or title,"queryPurpose":request.purpose,"retrievedAt":datetime.now(timezone.utc).isoformat(),"contentConfidence":"LOW","platformMetadata":{"bvid":bvid,"contentKind":"video"}})
        if len(output) >= request.limit: break
    return {"items": output}
