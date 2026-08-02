import json
from uuid import UUID
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from .settings import settings


class SelectedSong(BaseModel):
    recording_id: UUID
    reason: str = Field(min_length=8, max_length=120)


class Selection(BaseModel):
    selected: list[SelectedSong]
    candidate_summary: str = Field(min_length=12, max_length=240)


class DeepSeekCandidateSelector:
    def __init__(self):
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model, api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com", temperature=0.35,
        )

    async def select(self, preference: str, size: int, recordings: list[dict]) -> Selection | None:
        if self.model is None: return None
        catalog = [{"recordingId": x["id"], "title": x["title"], "artist": x["artistName"], "album": x["albumTitle"]} for x in recordings]
        prompt = f"""你是音乐偏好候选池生成助手。只可从给定目录中选择 {size} 首歌，绝不能编造歌曲或 ID。
用户偏好：{preference}
目录：{catalog}
只输出 JSON，不要 Markdown：{{"selected":[{{"recording_id":"uuid","reason":"中文理由"}}],"candidate_summary":"中文总结"}}。"""
        response = await self.model.ainvoke(prompt)
        return Selection.model_validate(json.loads(response.content))
