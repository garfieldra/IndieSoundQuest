from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal
from uuid import UUID

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from .report_schemas import ArtistRecommendation, PreferenceReport, SongRecommendation
from .settings import settings

logger = logging.getLogger(__name__)


class ReportDecision(BaseModel):
    action: Literal["analyze_tournament", "search_web", "search_domestic_content", "search_knowledge", "draft_report", "critique_report", "submit_report", "finish_degraded"]
    decision_summary: str = Field(min_length=4, max_length=120)
    query: str | None = Field(default=None, max_length=300)
    provider: Literal["ZHIHU", "BILIBILI", "DOUBAN"] | None = None


class ReportGenerator:
    def __init__(self) -> None:
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0.25,
            model_kwargs={"response_format": {"type": "json_object"}},
            max_retries=1,
        )

    async def decide(self, board_summary: dict[str, Any]) -> ReportDecision:
        if self.model is None:
            if not board_summary["analyzed"]:
                return ReportDecision(action="analyze_tournament", decision_summary="先分析本场赛事事实")
            if not board_summary["webSearched"]:
                return ReportDecision(action="search_web", decision_summary="补充跨艺人探索资料")
            if not board_summary["drafted"]:
                return ReportDecision(action="draft_report", decision_summary="依据现有证据生成报告")
            if not board_summary["critiqued"]:
                return ReportDecision(action="critique_report", decision_summary="执行独立质量审查")
            return ReportDecision(action="submit_report", decision_summary="提交已通过审查的报告")
        prompt = f"""你是赛后报告 Agent 的 Supervisor，采用 ReAct，根据黑板观察只选择下一项动作，不输出思维链。
黑板摘要：{json.dumps(board_summary, ensure_ascii=False)}
动作白名单：analyze_tournament, search_web, search_domestic_content, search_knowledge, draft_report, critique_report, submit_report, finish_degraded。仅当 blackboard 的 domesticResearchAvailable 非空时，才可自主选择 search_domestic_content，并指定其中一个 provider；它是可选研究工具，不是固定步骤。
决策原则：当前单场赛事事实是主要依据。只要尚未做赛事分析，先分析；分析完成后，若尚未有网络资料，应优先 search_web 来寻找本地目录外的跨艺人探索依据；网络检索完成后，可按报告表达需要选择 search_knowledge 补充主题解释。Milvus 仅补充主题解释与推荐理由，不能替代网络发现。不要选择 recentActions 中已有且对应事实已经存在的动作；例如 analyzed=true 时不得再选 analyze_tournament，webSearched=true 时不得再选 search_web，knowledgeSearched=true 时不得再选 search_knowledge。提交前必须已有分析、报告草稿，并至少审查一次。
当 action=search_web 时，query 必须给出一个具体中文检索词，优先使用 musicContext 中的艺人或胜者；其他动作可省略 query。
仅输出 JSON：{{"action":"...","decision_summary":"简短理由","query":"可选检索词"}}"""
        try:
            return ReportDecision.model_validate(json.loads(await self._invoke_json(prompt)))
        except (json.JSONDecodeError, ValidationError, TypeError):
            if not board_summary["analyzed"]:
                action = "analyze_tournament"
            elif not board_summary["drafted"]:
                action = "draft_report"
            elif not board_summary["critiqued"]:
                action = "critique_report"
            else:
                action = "submit_report"
            return ReportDecision(action=action, decision_summary="决策输出无效，选择满足护栏的安全动作")

    async def generate(self, facts: dict[str, Any], signals: list[dict[str, Any]], tournament_id: UUID, version: int, network_sources: list[dict[str, Any]] | None = None, knowledge_context: list[dict[str, Any]] | None = None) -> PreferenceReport | None:
        if self.model is None:
            return None
        compact_facts = {
            "tournamentId": facts.get("tournamentId"),
            "size": facts.get("size"),
            "entries": facts.get("entries", []),
            "matches": facts.get("matches", []),
        }
        prompt = f"""你是音乐赛事偏好报告 Agent。只能依据提供的赛事事实和偏好信号写中文报告。
必须只输出一个合法 JSON 对象，不能输出 Markdown、解释、注释或多余文字。
禁止推断心理疾病、教育背景、职业、收入或确定性人格类型。人格彩蛋只能是80到120字的娱乐性观察，并明确仅基于本场音乐选择。
推荐歌曲和艺人只能使用输入 entries 中出现的规范 ID；不得编造 ID。歌曲推荐必须是5到7首；目录只有单一艺人时艺人推荐可只给1位。
字段必须使用 camelCase：schemaVersion="1.0"、tournamentId、tournamentVersion、summary、dimensions、songRecommendations、artistRecommendations、choiceTrajectory、explorationTags、personalityEasterEgg、disclaimer、warnings。
summary 长度80到420字；dimensions 必须有3到5项，每项有 name、confidence（low/medium/high）、至少20字 explanation、至少1条 evidence；songRecommendations 总数必须是5到7首、每项至少8字 reason；explorationTags 必须是2到5个短标签；personalityEasterEgg 必须是80到120个中文字符；disclaimer 必须包含“仅基于”。
必须逐一阅读所有 matches，而非只根据冠军写报告。choiceTrajectory 选择3到5条最能说明选择轨迹的真实对局；每项必须逐字使用 matches 中的 matchId、roundNumber、matchIndex、winnerEntryId 及 left/rightEntryId 映射出的 winnerTitle、winnerArtistName、loserTitle、loserArtistName，signalRole 只能是 stable_anchor/preference_boundary/near_finalist，derivedNote 仅描述这一场音乐取舍，不得推断现实身份或人格。至少包含一条冠军晋级路径和一条两首不同作品的直接偏好边界（赛事规模允许时）。
推荐项有两种来源。catalog_verified 只能使用 entries 中的规范 ID：歌曲需要 recordingId，艺人需要 artistId。web_discovered 仅在网络资料明确提到该歌曲/艺人时才能使用：不可填写 recordingId/artistId，必须填写 title（歌曲）、artistName、sourceUrl、sourceTitle、searchQuery；sourceUrl 必须逐字取自网络资料。网络发现项必须在 reason 中说明“网络发现，待核验”，不可写成已入库或已验证。
若网络资料非空，且其中明确出现了本地 entries 之外的歌手或歌曲，推荐中必须优先包含 2 到 5 个 web_discovered 项，优先选择资料摘要中可逐字确认名称的对象；否则全部使用 catalog_verified。不要把网页本身的标题误当作歌曲名。
主题知识库资料只可用于 explanation、reason 和 2 到 5 个 explorationTags（短主题标签）；它不是新推荐的实体来源，不能把其中的歌手/歌曲当成网络发现，也不得输出相似度、内部 ID、原始摘要或歌词。
evidence 每项有 evidenceId、sourceType（match/vote/catalog/knowledge/web），优先使用偏好信号中已有的对局 evidenceId。
赛事事实：{json.dumps(compact_facts, ensure_ascii=False)}
偏好信号：{json.dumps(signals, ensure_ascii=False)}
网络资料（可为空；为空时不要生成 web_discovered）：{json.dumps(network_sources or [], ensure_ascii=False)}
主题知识库上下文（可为空；只作解释增强）：{json.dumps(knowledge_context or [], ensure_ascii=False)}
"""
        content = await self._invoke_json(prompt)
        try:
            return self._validate(content)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            errors = self._error_summary(exc)
            logger.warning("report model response rejected by contract: %s", errors)
            repair_prompt = f"""将下面的候选报告修复为唯一合法的 JSON 对象。不要写解释或 Markdown。
只修复格式、字段名、长度、数量和枚举值；不允许新增不在赛事事实中的歌曲或艺人 ID；web_discovered 的 sourceUrl 只能从给定网络资料中选择。
必须满足这些校验错误：{errors}。此外，explorationTags 只能保留 2 到 5 个短标签；personalityEasterEgg 必须重写成 80 到 120 个中文字符的娱乐性观察；不要删除 disclaimer。
候选报告：{content}
可用网络资料：{json.dumps(network_sources or [], ensure_ascii=False)}
"""
            try:
                return self._validate(await self._invoke_json(repair_prompt))
            except (json.JSONDecodeError, ValidationError, TypeError) as repair_error:
                logger.warning("report model repair rejected by contract: %s", self._error_summary(repair_error))
                return None

    async def _invoke_json(self, prompt: str) -> str:
        response = await self.model.ainvoke(prompt)
        content = str(response.content).strip()
        if content.startswith("```json") and content.endswith("```"):
            content = content[7:-3].strip()
        if not content.startswith("{"):
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                content = content[start:end + 1]
        return content

    async def discover_from_web(self, sources: list[dict[str, Any]]) -> tuple[list[SongRecommendation], list[ArtistRecommendation]]:
        """网络发现子 Agent：只从已检索到的资料抽取，输出仍需经过来源白名单校验。"""
        if self.model is None or not sources:
            return [], []
        prompt = f"""你是音乐网络发现子 Agent。只输出一个 JSON 对象，格式为 {{"songs":[],"artists":[]}}。
从下列网络资料中找 1 至 2 个明确出现、且不是安溥/张悬的音乐探索对象。绝不可利用常识补充资料中没出现的名称。
每个歌曲项必须有 title、artistName、sourceUrl、sourceTitle、searchQuery、reason；每个艺人项必须有 artistName、sourceUrl、sourceTitle、searchQuery、reason。reason 必须包含“网络发现，待核验”。sourceUrl 必须逐字复制自资料。若资料没有明确对象，返回空数组。
资料：{json.dumps(sources, ensure_ascii=False)}"""
        try:
            data = json.loads(await self._invoke_json(prompt))
            allowed_urls = {source.get("sourceUrl") for source in sources}
            songs = []
            for item in data.get("songs", [])[:5]:
                item["sourceStatus"] = "web_discovered"
                if item.get("sourceUrl") in allowed_urls:
                    songs.append(SongRecommendation.model_validate(item))
            artists = []
            for item in data.get("artists", [])[:3]:
                item["sourceStatus"] = "web_discovered"
                if item.get("sourceUrl") in allowed_urls:
                    artists.append(ArtistRecommendation.model_validate(item))
            if songs or artists:
                return songs, artists
            # Regex-only extraction from a general search snippet can turn an
            # unrelated phrase into an apparently valid "artist - song" pair.
            # With no model-confirmed entity, return nothing and keep catalog
            # recommendations rather than presenting a false music link.
            return [], []
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning("web discovery response rejected: %s", self._error_summary(exc))
            return [], []

    @staticmethod
    def _validate(content: str) -> PreferenceReport:
        data = json.loads(content)
        # The model occasionally overshoots presentation-only limits even when
        # its report is otherwise valid.  Normalising these non-factual fields
        # avoids spending a second model call solely to trim a tag list or an
        # entertainment blurb; it never creates recommendations or evidence.
        if isinstance(data, dict):
            tags = data.get("explorationTags")
            if isinstance(tags, list):
                data["explorationTags"] = [str(tag).strip()[:30] for tag in tags[:5] if str(tag).strip()]
            easter_egg = data.get("personalityEasterEgg")
            if isinstance(easter_egg, str):
                easter_egg = easter_egg.strip()
                if len(easter_egg) < 80:
                    easter_egg += "这只是从本场投票里读到的一点趣味倾向，请把它当作下一次继续听歌的小提示。"
                if len(easter_egg) > 120:
                    easter_egg = easter_egg[:119].rstrip("，、；：") + "。"
                data["personalityEasterEgg"] = easter_egg
        return PreferenceReport.model_validate(data)

    @staticmethod
    def _error_summary(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "; ".join(".".join(str(part) for part in item["loc"]) + ":" + item["type"] for item in error.errors()[:8])
        return type(error).__name__
