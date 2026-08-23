# Agent 工作流规格

**状态：** v0.2
**最后更新：** 2026-08-10
**上级规格：** `00-系统总体设计与规格路线图.md`  
**依赖规格：** `03-MVP产品规格：歌曲世界杯与音乐探索.md`、`04-音乐实体模型与跨平台匹配.md`

## 1. 目标与边界

Agent 服务只承载两个业务 Agent：

1. 候选池生成 Agent：根据用户输入生成世界杯候选池及补位队列；
2. 赛后报告 Agent：根据单场赛事事实生成详细偏好报告和下一步推荐。

长期音乐偏好档案是 Java 管理的辅助记忆，可作为弱背景输入，但不构成第三个 Agent，也不能取代当前赛事事实。

Agent 不负责账号、权限、赛程生成、投票落库、赛事状态迁移或外部资源映射。这些均由 Java 业务服务负责。

## 2. 总体设计

```mermaid
flowchart LR
  J["Java 业务服务"] --> A["FastAPI Agent API"]
  A --> G["LangGraph Supervisor ReAct Runtime"]
  G --> T1["音乐实体工具"]
  G --> T2["Milvus 知识库工具"]
  G --> T3["网络资料查询工具"]
  G --> T4["赛事/偏好只读工具"]
  T1 --> J
  T2 --> M[("Milvus")]
  T3 --> W["可信网页来源"]
  T4 --> J
  G --> V["不可绕过的运行时护栏"]
  V --> J
```

### 2.1 全局编排硬约束

- 所有业务 Agent 必须采用 Supervisor ReAct 或能力等价的“决策—行动—观察—再决策”循环；
- 每轮由模型基于目标、黑板、工具结果和剩余预算输出结构化 `action`，运行时负责校验并执行；
- 禁止使用固定顺序的工具调用链或 LangGraph 线性节点来冒充 Agent；
- 事实快照读取、最终 Schema 校验、权限、安全与预算检查可以固定，但它们是运行时护栏，不是 Agent 主编排；
- 不记录或展示隐含思维链，只保留短决策摘要、动作、参数摘要、观察与错误；
- 可使用专用工具执行器或子 Agent，但对产品只存在本规格定义的两个业务 Agent。

## 3. Agent 清单

| Agent | 触发时机 | 输入 | 输出  | 是否改变业务数据 |
| --- | --- | --- | --- | --- |
| `candidate_pool_generator` | 用户请求 AI 生成候选池 | 用户兴趣描述、赛事规模、排除项、可选长期偏好 | 32/64 首候选（含等量补位）、说明、来源 | 否 |
| `tournament_report_generator` | 单场赛事完成后 | 不可变赛事事实快照、可选长期偏好 | 详细报告、5–7 首歌曲和 2–3 位艺人推荐、娱乐性彩蛋 | 否 |

Java 服务负责保存 Agent 返回的版本化结果；Agent 只能返回建议，不能直接写入 MySQL。

## 4. 通用 Agent 状态

所有 LangGraph 状态都必须可序列化、可追踪，并禁止保存完整歌词、音频二进制或受限 Provider 原始内容。

```python
class AgentState(TypedDict):
    request_id: str
    workflow: str
    user_id: str | None
    locale: str
    input: dict
    resolved_entities: list[dict]
    candidate_recordings: list[dict]
    user_signals: list[dict]
    verified_claims: list[dict]
    web_sources: list[dict]
    evidence: list[dict]
    draft_output: dict | None
    validation_errors: list[dict]
    warnings: list[str]
    trace_id: str
    current_action: str | None
    action_history: list[dict]
    observations: list[dict]
    remaining_budget: dict
    termination_reason: str | None
```

状态设计要求：

- `request_id` 由 Java 生成，用于幂等和审计；
- 每个节点只追加或替换自己拥有的状态字段；
- `evidence` 必须可追溯到实体 ID、知识库 Claim ID 或 URL；
- 不向前端暴露模型内部推理链，仅暴露结构化阶段事件和用户可读摘要；
- `action_history` 只记录结构化决策摘要，不保存 chain-of-thought；
- 状态持久化策略由实现决定，但可恢复状态只能保留必要业务上下文与来源引用。

## 5. 受控工具契约

### 5.1 音乐实体工具

规范实体写入与外部资源映射必须经过 Java 的实体/Provider 层。Agent 可以决定调用“MusicBrainz 解析并导入”工具，但实际网络访问、限流、消歧、幂等落库仍由 Java 执行。

| 工具 | 输入 | 输出 | 限制 |
| --- | --- | --- | --- |
| `resolve_artist` | `query`、`locale` | 已消歧艺人候选 | 只能返回内部 UUID、MBID、名称、置信度 |
| `list_artist_recordings` | `artist_id`、`limit`、筛选条件 | 可用 Recording 列表 | 只返回符合版本规则的规范实体 |
| `get_recording_context` | `recording_ids` | 专辑、发行、试听可用性、外链 | 不返回音频内容或完整歌词 |
| `get_entity_relations` | `entity_id`、关系类型 | 已验证关系 | 知识图谱上线前返回空或结构化降级 |
| `resolve_and_import_recordings` | 歌名、艺人名、来源 URL | 已解析的内部 Recording UUID、MBID、置信度 | Java 统一执行 MusicBrainz 查询、1 req/s 限流、消歧与幂等导入 |

### 5.2 Milvus 知识库工具

Milvus 存储的不是未经筛选的网页全文，而是具有来源、实体关联和审核状态的音乐知识片段。

| 工具 | 输入 | 输出 | 限制 |
| --- | --- | --- | --- |
| `search_verified_knowledge` | 查询、实体 ID、`top_k` | Claim 片段、Claim ID、来源、分数 | 默认只检索 `reviewed` 或可公开使用的资料 |
| `get_claim_sources` | Claim ID 列表 | URL、标题、来源类型、发布时间 | 不返回完整受版权文章 |

每个 Claim 至少包含：`subject_entity_id`、`predicate`、`summary`、`source_url`、`source_type`、`review_status`。

### 5.3 网络资料查询工具

网络查询仅用于补充当前知识库没有的、与用户目标直接相关的公开背景资料。

```text
search_web(query, allowed_source_types, max_results)
fetch_source_summary(url)
```

规则：

1. 优先顺序为艺人/唱片公司/音乐节官方资料、正式采访、可信署名媒体；
2. 返回 URL、标题、发布时间、来源类型和受限长度的摘要；
3. 不下载音频、不爬取歌词、不绕过登录、付费墙或 robots 限制；
4. 网页资料只作为临时证据，需进入审核流程后才可写入长期 Milvus；
5. 网络查询失败时，工作流使用已验证知识库继续，不得凭空补全事实。

### 5.4 赛事与用户信号工具

| 工具 | 输入 | 输出 | 权限 |
| --- | --- | --- | --- |
| `get_tournament_snapshot` | `tournament_id` | 不可变候选池、对局、投票、状态 | 仅赛事所属用户 |
| `get_user_taste_signals` | `user_id`、范围 | 已聚合的匿名化音乐偏好信号 | 仅本人 |
| `get_profile_evidence_count` | `user_id` | 有效赛事数、有效投票数 | 仅本人 |

这些工具为只读。创建赛事、确认候选、写入投票、删除历史和保存报告均通过 Java API 完成。

## 6. Agent 一：世界杯候选池生成

### 6.1 输入

```json
{
  "requestId": "uuid",
  "interest": "我喜欢张悬，也想听温柔但不甜腻的华语独立音乐",
  "size": 16,
  "constraints": {
    "excludeRecordingIds": [],
    "preferUnheard": false,
    "allowCollaborations": true
  }
}
```

`size` 在 MVP 只能为 `16` 或 `32`。Agent 的目标候选量固定为 `size * 2`：前一半用于首屏确认，后一半作为用户移除歌曲后的低成本补位队列。

### 6.2 Supervisor ReAct 循环

```mermaid
flowchart LR
  A["初始化目标、黑板与预算"] --> S["Supervisor 决定下一动作"]
  S -->|search_catalog| C["检索本地目录"]
  S -->|search_knowledge| K["检索 Milvus"]
  S -->|search_web| W["Tavily 网络发现"]
  S -->|resolve_musicbrainz| M["Java MusicBrainz 解析并导入"]
  S -->|rerank| R["候选重排与去重"]
  S -->|submit| V["最终护栏校验"]
  C --> O["写入观察"]
  K --> O
  W --> O
  M --> O
  R --> O
  O --> S
  V -->|不通过且有预算| S
  V -->|通过或预算耗尽| E["返回候选草稿/结构化不足"]
```

Supervisor 每轮只能返回动作白名单中的一项，并附带简短 `decision_summary`、结构化参数和预期信息增益。运行时拒绝非法动作、重复无收益调用和超预算调用。具体工具是否调用、调用次序与次数不能预先写死。

允许动作：`search_catalog`、`search_knowledge`、`search_web`、`resolve_musicbrainz`、`rerank_candidates`、`submit_candidates`、`finish_insufficient`。

典型但非固定的自主行为包括：本地目录不足时搜索相近艺人/风格；Tavily 发现目录外歌曲后提取“歌名 + 艺人 + 来源”，再要求 Java 经 MusicBrainz 解析为规范实体；发现同名或版本歧义时继续查询而不是直接采用。

### 6.3 候选生成与扩展规则

- 不以“模型记忆”直接生成最终歌曲；最终候选必须具有 Java 返回的内部 `recording_id`；
- 每个候选必须是唯一的规范 `recording_id`；
- 16 首赛事目标生成 32 首候选；32 首赛事目标生成 64 首候选；
- 本地目录不足时，应自主扩大到语义相近的歌曲、艺人或风格，再使用 Tavily 与 MusicBrainz 补足；相近范围不要求机械地限定同艺人；
- Tavily 结果不得直接进入候选池，必须经 MusicBrainz 消歧并由 Java 幂等导入；
- 若可用且资料充分，候选池应包含代表作与非代表作，避免只按热度排列；
- 资料不足时，说明中必须降低表述强度，例如“本场主要按发行时期与可用曲目组织”；
- 达到 16/32 首可开赛数量但不足两倍目标时，可返回较短补位队列并明确警告；用尽预算仍不足 16/32 首时才返回 `INSUFFICIENT_CANDIDATES`；
- 返回的是赛事草稿，用户确认前不创建赛事快照。

### 6.4 输出契约

```json
{
  "requestId": "uuid",
  "status": "ready_for_confirmation",
  "artistId": "uuid",
  "size": 16,
  "recordingIds": ["32 个内部 uuid"],
  "activeRecordingIds": ["前 16 个内部 uuid"],
  "reserveRecordingIds": ["后 16 个内部 uuid"],
  "generationSummary": "这场赛事将…",
  "coverage": [
    {"dimension": "release_period", "description": "覆盖早期与后期作品"}
  ],
  "evidence": [
    {"claimId": "uuid", "sourceUrl": "https://...", "supports": "coverage"}
  ],
  "warnings": []
}
```

## 7. Agent 二：赛后报告与推荐

### 7.1 触发条件

- Java 服务确认赛事状态为 `COMPLETED`；
- 用户显式点击“生成探索总结”，或完成探索世界杯后自动触发；
- 对同一 `tournament_id + tournament_version` 保持幂等，重复请求返回同一版本结果或可重试状态。

### 7.2 Supervisor ReAct 循环

```mermaid
flowchart LR
  A["护栏读取不可变赛事事实"] --> S["Supervisor 决定下一动作"]
  S -->|analyze_votes| V["分析晋级与对局信号"]
  S -->|search_catalog| C["检索目录内推荐"]
  S -->|search_knowledge| K["检索 Milvus 事实"]
  S -->|search_web| W["Tavily 外部发现"]
  S -->|resolve_musicbrainz| M["解析目录外歌曲/艺人"]
  S -->|draft_report| D["生成结构化报告草稿"]
  S -->|critique_report| Q["独立 Critic 工具"]
  S -->|submit| G["最终护栏校验"]
  V --> O["写入观察"]
  C --> O
  K --> O
  W --> O
  M --> O
  D --> O
  Q --> O
  O --> S
  G -->|不通过且有预算| S
  G -->|通过或终止| H["返回版本化报告"]
```

允许动作：`analyze_tournament`、`read_long_term_profile`、`search_catalog`、`search_knowledge`、`search_web`、`resolve_musicbrainz`、`draft_report`、`critique_report`、`submit_report`、`finish_degraded`。除事实快照读取和最终护栏外，是否查询长期档案、Milvus、网络或 MusicBrainz 均由 Supervisor 根据当前证据缺口决定。

报告提交前必须已有草稿并至少通过一次 Critic；这是质量门禁，不规定其他工具的调用顺序。Critic 可以作为受限子 Agent/工具执行器存在，但不是第三个产品业务 Agent。

### 7.3 信号提取

输入是不可变赛程和投票，不是模型对歌曲的主观臆测。可提取：

- 冠军、前四、每首作品的晋级轮次；
- 用户显式选择的投票理由；
- 作品的已验证元数据：发行时间、关联专辑、参与艺人、标签；
- 用户在相同维度对局中的重复选择。

不得从单次投票推断稳定人格或真实生活状态。

### 7.4 推荐策略

目标输出 5–7 首歌曲和 2–3 位艺人，允许模型在证据不足时减少数量。候选可来自：

1. 同艺人的未参赛 Recording 或 Release Group；
2. 与获胜作品共享已验证制作人、流派、时期、厂牌或音乐关系的艺人/作品；
3. 知识图谱上线后可使用多跳关系，但每一跳须可解释；
4. 网络发现并经 MusicBrainz/Java 规范化后的目录外歌曲或艺人；
5. 无充分依据时少推荐或只给同艺人下一步，不凑数量。

每项推荐必须具有 `recommendation_type`、`target_entity_id`、`reasoning`、`evidence[]` 和 `confidence`。`reasoning` 必须同时引用用户选择和音乐依据；不能写成“因为你是某种人”。

### 7.5 输出契约

```json
{
  "tournamentId": "uuid",
  "resultVersion": 1,
  "scope": "single_tournament",
  "summary": {
    "text": "在本场赛事中…",
    "evidenceCount": 15,
    "confidence": "low"
  },
  "signals": [
    {"dimension": "release_period", "observation": "…", "supportingVoteIds": ["uuid"]}
  ],
  "recommendations": [
    {
      "type": "same_artist_next",
      "targetEntityId": "uuid",
      "reasoning": "…",
      "evidence": [{"sourceType": "user_vote"}, {"claimId": "uuid"}],
      "confidence": "medium"
    }
  ],
  "warnings": ["本结论仅基于本场赛事"]
}
```

## 8. 模型与输出控制

### 8.1 模型 Provider

- 默认 Provider：DeepSeek；
- Agent 服务通过统一 `ChatModelProvider` 接口调用模型；
- Provider 配置从环境变量读取，模型名称、超时、最大 Token、重试策略可配置；
- 模型替换不应改变工具契约和业务输出 JSON Schema。

### 8.2 结构化输出

所有对 Java 的最终返回均需通过 Pydantic 模型校验。模型输出解析失败时：

1. 进行一次受限格式修复；
2. 仍失败则返回结构化 `MODEL_OUTPUT_INVALID`；
3. 不把原始模型文本直接写入业务数据库或呈现为正式报告。

### 8.3 提示词原则

- 系统提示词明确区分“已验证事实”“临时网页来源”“用户选择推断”；
- 不要求模型暴露思维链；
- 要求未知时承认未知；
- 要求推荐少而有据；
- 所有用户可见音乐结论使用简体中文；
- 提示词、模型和工具版本写入追踪元数据。

## 9. 流式事件与用户可见状态

Java 调用 Agent 后可通过 SSE 将以下事件转发给 React：

| 事件 | 用户可见文案示例 | 是否包含内部细节 |
| --- | --- | --- |
| `stage_started` | “正在整理可用曲目” | 否 |
| `sources_found` | “找到若干可引用的音乐资料” | 仅数量和来源类型 |
| `draft_ready` | “候选赛事已准备好，请确认” | 返回候选与说明 |
| `insight_ready` | “本场探索总结已生成” | 返回结构化总结 |
| `warning` | “部分歌曲暂无试听，已提供外链” | 返回可操作提示 |
| `failed` | “暂时无法完成资料整理，请稍后重试” | 不暴露堆栈、密钥或模型原文 |

不向用户推送隐含推理、Prompt、检索评分或供应商密钥信息。

## 10. 失败、降级与安全

| 场景 | Agent 行为 | 用户体验 |
| --- | --- | --- |
| MusicBrainz 个别实体无结果 | 丢弃该候选并继续自主搜索相近方向 | 不把未解析歌曲放入候选池 |
| 候选不足两倍但达到开赛数 | 返回可开赛候选和较短补位队列 | 明确补位数量较少 |
| 候选不足 16/32 | 用尽目录、知识库、Tavily、MusicBrainz 与相近方向预算后停止 | 建议调整兴趣描述或自选补充 |
| Milvus 无命中 | 跳过知识库，按元数据组织候选 | 降低解释强度 |
| 网络查询失败 | 使用已验证资料继续 | 显示“部分背景资料暂不可用” |
| 模型超时/限流 | 返回可重试状态；Java 可保留草稿 | 不影响已开始赛事 |
| 推荐证据不足 | 缩减推荐数量 | 不输出无依据推荐 |
| 敏感属性推断 | 校验节点拒绝输出 | 返回仅音乐维度的安全总结 |
| 用户无权读取赛事 | 工具拒绝调用 | 返回通用无权限错误 |

注意：MVP 的合法赛事规模固定为 16 或 32；候选不足时不能擅自生成 8 首赛事。

## 11. 可观测性与评估

每次 Agent 调用至少记录：

```text
request_id, trace_id, agent_name, user_id_hash, tournament_id,
model_provider, model_name, prompt_version, tool_calls,
latency_ms, input_tokens, output_tokens, estimated_cost,
validation_result, warning_codes, result_version,
iteration_count, termination_reason, remaining_budget
```

建议采用 Langfuse 追踪模型、工具调用、Token 和成本；应用日志与指标通过 OpenTelemetry 统一关联 `trace_id`。不得记录完整用户偏好文本、API Key、完整歌词或受限媒体 URL。

离线评估指标：

| 指标 | 说明 |
| --- | --- |
| 候选唯一性 | 候选池中不重复 Recording 的比例，应为 100% |
| 版本冲突率 | Live/Remaster 等近重复版本被错误同时纳入的比例 |
| 来源覆盖率 | 用户可见关键事实中带来源或用户选择标识的比例 |
| 推荐可解释率 | 推荐同时引用用户信号和音乐依据的比例 |
| 无依据断言率 | 没有来源/信号支撑的事实陈述比例，应趋近 0 |
| 敏感推断拦截率 | 安全校验拦截的不合规输出数量 |
| 端到端延迟 | 创建草稿、生成赛后总结、生成整体报告的耗时 |

## 12. 验收标准

- [ ] 系统只暴露候选池生成与赛后报告两个业务 Agent；
- [ ] 两个 Agent 均由结构化 Supervisor ReAct 循环决定工具和顺序，不存在固定线性主工作流；
- [ ] Agent 只使用白名单工具，不能直接访问业务数据库；MusicBrainz 的落库访问统一经过 Java Provider；
- [ ] 候选结果只包含 Java 实体服务返回的规范 `recording_id`；
- [ ] 16/32 首世界杯分别以 32/64 首总候选为目标，并拆分 active/reserve；
- [ ] 赛事未完成时不能生成正式赛后洞察；
- [ ] 单场总结明确标注“仅基于本场赛事”；
- [ ] 每条推荐都有本场用户信号和音乐依据，长期档案只能作为弱背景；
- [ ] 输出经过 Pydantic JSON Schema 校验；
- [ ] 模型、网络、知识库失败均返回用户可理解的结构化降级状态；
- [ ] Agent 无法写入赛事、投票、用户资料或 Provider Mapping；
- [ ] Agent 追踪数据可通过 `request_id` 与 Java 请求关联。
- [ ] 追踪可证明不同输入可以产生不同合法工具序列，并记录预算与终止原因。

## 13. 不在本规格范围内

- Java 领域模型、事务、幂等锁和 SSE 转发实现；
- Milvus Collection Schema、入库管道、人工审核后台；
- 网络查询供应商的具体实现和凭据；
- 知识图谱数据模型与 Neo4j 查询；
- 具体 Prompt 文本、模型参数和代码目录；
- 前端赛事页面与 Agent 输出呈现细节。
