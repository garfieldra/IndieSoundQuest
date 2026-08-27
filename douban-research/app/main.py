from __future__ import annotations
import asyncio, json, re
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="IndieSoundQuest Douban Research Sidecar")
class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    purpose: str
    limit: int = Field(default=5, ge=1, le=8)
def compact(value, limit): return " ".join(re.sub(r"<[^>]+>", " ", str(value or "")).split())[:limit]
@app.get("/health")
async def health(): return {"status":"UP", "authenticated":False, "mode":"anonymous-lookup-only"}
@app.post("/v1/research/search")
async def search(request: SearchRequest):
    if request.purpose not in {"candidate_discovery","cultural_context","recommendation_validation"}: raise HTTPException(422, "invalid research purpose")
    process = await asyncio.create_subprocess_exec("douban", "search", request.query, "--type", "music", "--limit", str(request.limit), "-o", "json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try: stdout, _ = await asyncio.wait_for(process.communicate(), timeout=12)
    except TimeoutError: process.kill(); await process.communicate(); raise HTTPException(503, "PLATFORM_UNAVAILABLE")
    if process.returncode not in {0,3}: raise HTTPException(503, "PLATFORM_UNAVAILABLE")
    try: items = json.loads(stdout.decode())
    except json.JSONDecodeError: items = []
    if isinstance(items, dict): items = items.get("items", items.get("results", []))
    output=[]
    for item in items if isinstance(items,list) else []:
        url=str(item.get("url") or "")
        if not url.startswith("https://music.douban.com/"): continue
        title=compact(item.get("title") or item.get("name"),180)
        if not title: continue
        output.append({"provider":"DOUBAN","sourceType":"MUSIC_SUBJECT","sourceUrl":url,"title":title,"authorDisplayName":None,"snippet":compact(item.get("summary") or item.get("subtitle") or title,280),"queryPurpose":request.purpose,"retrievedAt":datetime.now(timezone.utc).isoformat(),"contentConfidence":"LOW","platformMetadata":{"subjectId":str(item.get("id") or ""),"contentKind":"music_subject"}})
    return {"items":output[:request.limit]}
