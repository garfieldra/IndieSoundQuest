# 候选歌曲 Agent：证据驱动探索与质量闸门

> 状态：开发中  
> 版本：v1.0  
> 前置规格：`10-候选池Agent质量评测与优化.md`、`11-Agent外部歌曲发现与目录扩容.md`、`09-Milvus知识库与事实治理规格.md`、`11-双Agent-ReAct运行时落地规格.md`

## 1. 目标

把候选歌曲生成 Agent 从“能生成已核验候选”的 ReAct 原型，深化为有证据、可自主扩展、可审计且可持续优化的探索系统。

它仍是项目仅有的两个业务 Agent 之一，不新增“音乐知识 Agent”“搜索 Agent”等面向用户的第三业务 Agent。Milvus、Wikidata、网页搜索、MusicBrainz 与质量审查均为同一候选歌曲 Agent 自主选择的 Tool。

> 实现进度（2026-08-22）：已完成候选响应的“探索依据 + 来源摘要 + 精简工具摘要”契约，以及 Java 安全过滤透传和前端折叠展示；Milvus 实体知识、Wikidata Tool、运行记录持久化与质量闸门仍待继续实现，故本规格保持“开发中”。

用户输入偏好后，Agent 应当：

1. 从本地曲库与知识库优先召回；
2. 在证据或数量不足时，自主决定是否扩大到跨艺人、跨语种、网页发现或结构化背景知识；
3. 将目录外线索通过 MusicBrainz 消歧并导入内部曲库；
4. 最多进行 5 轮有效扩展；
5. 对候选池执行硬性质量闸门；
6. 向用户展示精简、可理解的“为什么推荐”和来源摘要，而非隐藏推理过程；
7. 保存精简运行记录，供调试、评测与面试演示使用。

## 2. 已确认决策

| 项目 | 决策 |
| --- | --- |
| 用户可见解释 | 每首候选展示推荐依据与来源摘要 |
| 自主扩展上限 | 最多 5 轮“有效扩展”；无新增有效证据的调用不消耗成功轮次但会累积停滞计数 |
| 运行记录 | 先保存精简、结构化记录；不保存 Prompt、网页全文、隐式思维链 |
| 可用证据 | 音乐特征、文化语境、歌词主题、创作背景、公开采访与可信乐评摘要 |
| 探索范围 | 未明确限定艺人时允许跨艺人、跨语种扩展 |
| 新工具 | 接入 Milvus 本地知识检索、Wikidata 背景知识和候选质量审查 |
| MCP 路线 | 先实现内部受控 Tool，随后以同一服务契约提供 MCP Server 外壳 |

## 3. 产品体验

### 3.1 候选卡新增内容

每张候选卡在保持简洁的前提下，增加可展开的“探索依据”：

```text
为什么适合这场比赛
· 与「夜晚散步、克制叙事」的偏好相符：……
· 创作语境：……
· 已核验：MusicBrainz
· 参考：艺人访谈 / 编辑推荐（点击查看）
```

- 默认仅显示 1–2 条摘要，避免候选确认页变成资料墙；
- 来源只展示标题、域名和链接；不展示网页全文、搜索关键词、工具原始返回或模型思维链；
- 没有可靠外部证据时可仅显示“基于本地曲库标签与本场偏好匹配”；
- `ARTIST_LOCKED` 下，解释必须优先说明作品覆盖、时期、专辑或风格差异，不能伪装成跨艺人推荐；
- 解释是辅助理解，不是对人格、教育背景或心理状态的断言。

### 3.2 质量不足的反馈

在 5 轮扩展耗尽后仍不足正式位时，返回已有的 `insufficient_candidates`，附带面向用户的短说明，例如“已在中文独立音乐与相近语境中继续检索，但可核验歌曲不足以组成 32 首赛事”。

候补不足但正式位足够时，允许开赛，显示低干扰提示。不得用未经核验的网页歌曲凑数。

## 4. ReAct 工具层

### 4.1 原则

```text
Supervisor 读取 Observation
  → 自主选择一个 Tool
  → Tool 返回类型化事实或失败原因
  → 更新黑板与预算
  → 继续、提交质量审查或结束
```

- 安全校验、意图边界与最终提交是强制门禁；可选 Tool 不得组成固定流水线；
- 每次动作都携带枚举型 `reasonCode`，而非自由文本的隐藏推理；
- Tool 只接收 Pydantic/JSON Schema 参数，限制超时、返回条数、域名与请求预算；
- 所有网络返回都视为不可信输入，不直接成为歌曲事实或模型指令；
- `ARTIST_LOCKED` 的硬范围永远高于扩展、丰富度和数量。

### 4.2 Tool 清单

| Tool | 数据源/实现 | 可解决的问题 | 关键输出 |
| --- | --- | --- | --- |
| `search_local_catalog` | Java 内部曲库 | 已核验歌曲、封面、试听、别名 | 内部 Recording UUID 候选 |
| `search_knowledge` | Milvus + 已审核 Claim | 语义相似、场景、主题、创作背景 | 带 `claimId` 的短事实 |

联调验收使用 `Demos/verify_candidate_agent_knowledge_integration.py`：以真实 SSE 内部接口提交跨艺人偏好，并断言该次 ReAct 行动轨迹包含 `search_knowledge`。该断言只验证工具实际可被自主选择，不规定普通请求的固定行动顺序。
| `search_web_evidence` | Tavily，受信域名策略 | 访谈、乐评、官方介绍、文化语境 | `EvidenceSnippet` |
| `lookup_wikidata_context` | Wikidata Query Service | 艺人地域、时期、流派、关联实体 | 可引用的结构化背景 |
| `resolve_musicbrainz_entities` | MusicBrainz Search + Lookup | 网页发现线索的歌曲实体消歧 | `MB_VERIFIED/AMBIGUOUS/REJECTED` |
| `import_verified_entities` | Java 内部导入 API | 将外部已核验实体转为内部 UUID | `CATALOG_IMPORTED` |
| `validate_candidate_quality` | 内部确定性审查器 | 覆盖、重复、范围、实体、证据质量 | `QualityGateResult` |
| `rerank_candidates` | 本地确定性排序 + LLM 受限重排 | 将合法候选变成赛事正式位/候补 | 排序与理由草案 |

现有的试听解析、网易云搜索链接生成属于界面可达性能力，不是候选探索质量的决策工具。

### 4.3 不接入的能力

- **完整歌词抓取/保存**：首版不接入，避免版权、稳定性与中文平台接口风险；允许使用公开采访或评论中对主题的短摘要；
- **Google Programmable Search**：首版不与 Tavily 重复接入。后续如需多 Provider 容灾，再做 `WebSearchProvider` 配置化；
- **Last.fm 相似度/标签**：作为国际音乐补充源候选，不作为首版依赖；其中文独立音乐覆盖与语境稳定性不足；
- 非官方国内音乐平台接口、绕过访问控制的抓取能力：不接入。

## 5. 证据模型与来源治理

### 5.1 统一证据对象

```json
{
  "evidenceId": "ev_...",
  "sourceType": "LOCAL_CLAIM | WIKIDATA | WEB_EDITORIAL | WEB_OFFICIAL | MUSICBRAINZ",
  "sourceUrl": "https://...",
  "sourceTitle": "…",
  "publisherDomain": "…",
  "snippet": "不超过 280 字的事实性摘要",
  "claims": ["创作背景", "主题", "发行时期"],
  "entityRefs": [{"kind": "recording", "mbid": "..."}],
  "retrievedAt": "ISO-8601",
  "trustLevel": "HIGH | MEDIUM | LOW"
}
```

网页只保留必要摘要与 URL，不复制全文。用户可见的来源说明必须来自这个对象，不能由模型虚构。

### 5.2 来源优先级

```text
内部审核 Claim / MusicBrainz 规范实体
  > 艺人、唱片公司、音乐节等官方来源
  > 有署名编辑的媒体、采访与乐评
  > Wikidata（结构化背景，必要时回链参考）
  > 聚合站、论坛与无署名页面
```

低信任来源只能作为发现线索，不能单独支撑“创作背景”“歌词主题”等对用户呈现的事实性表述。

## 6. 五轮自主扩展与停止策略

### 6.1 有效扩展定义

一次扩展只有在以下任一结果发生时才记为 `effectiveExpansionCount + 1`：

- 新增至少一首 `CATALOG_IMPORTED` 的、未重复且符合范围的候选；
- 新增可支撑候选理由的 `HIGH/MEDIUM` 信任证据；
- 解除一个实体消歧、范围或语言/时期约束的不确定性。

最大有效扩展次数为 5。与此同时，连续两次网络/知识查询未新增有效结果时记为 `stagnation`；达到停滞阈值，Supervisor 必须改用不同策略、提交质量审查或结束，不能无界重试。

### 6.2 允许的扩展方式

| 意图 | 优先扩展 | 禁止行为 |
| --- | --- | --- |
| `ARTIST_LOCKED` | 同艺人不同专辑、时期、合作作品（若允许） | 加入范围外艺人 |
| `ARTIST_SEEDED` | 相近主题、制作人、场景、语种或关联艺人 | 无证据地把起点艺人目录全部填满 |
| `OPEN_DISCOVERY` | 由偏好面向决定跨艺人、跨语种扩展 | 把跨语种本身当作质量目标 |

跨语种只在能够解释与用户偏好的关联时采用；若用户指定中文、某语言或某地区，该约束优先。

## 7. 候选质量闸门

`validate_candidate_quality` 是提交前不可跳过的确定性门禁。输出既包含机器可读问题，也包含给 Supervisor 的下一步建议。

```json
{
  "passed": false,
  "activeCount": 28,
  "reserveCount": 11,
  "targetActiveCount": 32,
  "targetTotalCount": 64,
  "issues": [
    {"code": "ACTIVE_COUNT_INSUFFICIENT", "severity": "BLOCKING"},
    {"code": "EVIDENCE_COVERAGE_LOW", "severity": "WARNING"}
  ],
  "metrics": {
    "catalogVerifiedRatio": 1.0,
    "duplicateVersionCount": 0,
    "explainedCandidateRatio": 0.84,
    "sourceBackedCandidateRatio": 0.53
  },
  "nextBestActions": ["search_knowledge", "search_web_evidence"]
}
```

### 7.1 硬性拒绝条件

- 正式位少于赛事规模；
- 任一正式位没有内部 Recording UUID；
- 外部发现歌曲未完成 MusicBrainz 核验与 Java 导入；
- 违反 `ARTIST_LOCKED` 范围；
- 重复使用同一 Recording，或无明确版本标签地重复同一作品；
- 证据摘要含不受信网页指令、完整歌词或无法回链的事实。

### 7.2 软性警告

- 未达到两倍总候选量但正式位足够；
- 推荐依据覆盖率偏低；
- 作品过度集中且与意图不符；
- 多个候选只依赖同一弱来源；
- 可试听/封面信息缺失。

软警告不会阻止开赛，但必须进入用户提示与精简运行记录。

## 8. 精简运行记录与 MCP

### 8.1 持久化内容

以 `candidate_agent_run`、`candidate_agent_action`、`candidate_evidence` 为建议模型（具体表结构在编码前另立迁移设计）：

- Run：请求 ID、候选池 ID、模型/提示版本、开始结束时间、终止原因、有效扩展次数、质量摘要；
- Action：序号、Tool 名、枚举 `reasonCode`、耗时、成功/失败类别、返回数量、脱敏参数摘要；
- Evidence：统一证据对象、关联 Recording/Artist、信任等级；
- 不保存 API Key、完整 Prompt、网页全文、原始搜索响应、模型 Chain-of-Thought。

默认保留 30 天；首版只提供内部调试查询，不向普通用户展示整份运行记录。

### 8.2 MCP Server 外壳

内部 Tool 稳定后，Python Agent 服务可提供可选的 `music-exploration-mcp`，暴露相同的输入/输出 Schema：

```text
catalog.search
knowledge.search
web.searchEvidence
musicbrainz.resolveRecording
music.importVerifiedRecording
quality.validateCandidatePool
```

- MCP 仅复用内部服务权限，不绕过 Java 业务校验；
- 生产业务 Agent 继续调用内部 Python Tool Adapter，不要求运行 MCP Client；
- MCP 访问要有服务身份、请求限额、审计与只读默认权限；
- 首版可先交付 transport-independent 的 Tool contract，再增加 `stdio` 或 HTTP MCP transport。

## 9. 前后端与接口增量

### 9.1 候选池响应增量

每个候选项增加：

```json
{
  "explorationRationale": [
    {"kind": "preference_match", "text": "…", "evidenceIds": ["ev_1"]},
    {"kind": "creative_context", "text": "…", "evidenceIds": ["ev_2"]}
  ],
  "evidenceSummary": [
    {"title": "…", "domain": "…", "url": "…", "trustLevel": "HIGH"}
  ]
}
```

文本由后端 Schema 限制长度、条数与允许类别；前端只能渲染已返回字段。

### 9.2 运行状态增量

候选池顶层可返回非敏感摘要：

```json
{
  "explorationSummary": {
    "effectiveExpansionCount": 3,
    "toolsUsed": ["catalog", "knowledge", "web", "musicbrainz"],
    "qualityWarnings": []
  }
}
```

这不是过程直播，也不是隐藏推理展示；仅用于让用户理解“系统做了哪些资料核验”。

## 10. 验收标准

- [ ] Agent 仍是动态 ReAct，自主选择可选 Tool，未退化为固定链路；
- [ ] 16/32 首赛事分别以 32/64 首为目标，最多 5 次有效扩展；
- [ ] 已核验本地候选优先；目录外歌曲必须 MusicBrainz 消歧并经 Java 导入；
- [ ] Milvus、Wikidata、质量审查均作为独立受控 Tool 可被 Agent 按需调用；
- [ ] 用户能看到每首候选的简洁推荐依据与可点击来源摘要；
- [ ] 无可靠外部证据时不会伪造创作背景或歌词主题；
- [ ] `ARTIST_LOCKED` 不会被跨艺人/跨语种扩展突破；
- [ ] 质量闸门能拒绝不合法正式位，并对候补不足返回警告；
- [ ] 精简运行记录不包含密钥、Prompt、网页全文或 Chain-of-Thought；
- [ ] MCP Tool contract 与内部 Tool contract 一致，且不能绕过业务校验；
- [ ] 有 16/32、锁定艺人、开放探索、跨语种、网络消歧失败、五轮耗尽等自动化回归案例。
