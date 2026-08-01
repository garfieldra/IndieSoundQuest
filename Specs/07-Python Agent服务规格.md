# Python Agent 服务规格

**状态：** 草案 v0.1  
**最后更新：** 2026-08-01  
**上级规格：** `00-系统总体设计与规格路线图.md`  
**依赖规格：** `05-Agent工作流规格.md`、`06-Java业务服务规格.md`

## 1. 服务定位

Python Agent 服务是仅供 Java 业务服务调用的内部智能编排服务。它使用 FastAPI 提供受认证的 HTTP/SSE 接口，使用 LangGraph 执行工作流，并通过受控工具访问音乐实体、Milvus、网络资料与用户赛事快照。

它不直接面向浏览器开放，不连接 MySQL，不持有用户登录会话，也不拥有赛事或投票的写权限。

## 2. 技术基线

| 类别 | 选型 |
| --- | --- |
| 运行时 | Python 3.12 |
| Web 框架 | FastAPI + Uvicorn |
| 工作流 | LangGraph |
| LLM 抽象 | LangChain Core + 自定义 Provider Adapter |
| 默认模型 | DeepSeek（OpenAI 兼容接口） |
| 数据校验 | Pydantic v2 |
| HTTP 客户端 | httpx（调用 Java、搜索或资料服务） |
| 追踪 | Langfuse + OpenTelemetry（可配置启用） |
| 配置 | Pydantic Settings + 环境变量 |
| 部署 | Docker 容器，由 Docker Compose 编排 |

## 3. 代码结构

```text
agent-service/
├── app/
│   ├── main.py                    # FastAPI 应用、生命周期与路由注册
│   ├── api/
│   │   ├── dependencies.py        # 内部服务鉴权、请求上下文
│   │   ├── routes_health.py
│   │   └── routes_workflows.py    # 工作流启动、SSE 事件
│   ├── schemas/                   # Pydantic 请求、事件、结果模型
│   ├── graphs/
│   │   ├── exploration_planner.py
│   │   ├── tournament_insight.py
│   │   └── taste_profile.py
│   ├── nodes/                     # LangGraph 节点实现
│   ├── tools/
│   │   ├── music_catalog.py       # 调用 Java 内部实体接口
│   │   ├── tournament_context.py  # 调用 Java 只读赛事接口
│   │   ├── knowledge_search.py    # Milvus 检索抽象
│   │   ├── web_research.py        # 网络资料查询抽象
│   │   └── relation_graph.py      # 未来 Neo4j 抽象
│   ├── llm/
│   │   ├── provider.py            # ChatModelProvider 协议
│   │   ├── deepseek.py
│   │   └── factory.py
│   ├── prompts/                   # 版本化系统提示词和模板
│   ├── services/                  # 结果校验、引用组装、运行状态
│   ├── observability/             # trace、metrics、redaction
│   └── settings.py
├── tests/
│   ├── unit/
│   ├── contract/
│   └── integration/
├── pyproject.toml
├── Dockerfile
└── .env.example
```

## 4. 服务 API

所有业务接口只允许内部网络访问，并要求 Java 服务凭证。API 不接受浏览器用户 Token。

### 4.1 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health/live` | 进程是否存活；不检查外部依赖 |
| `GET` | `/health/ready` | 配置、模型 Provider、Java 内部接口和必要依赖是否可用 |

### 4.2 工作流流式接口

Java 以 `POST` 发起请求，并接收 `text/event-stream` 响应；Java 再将经过筛选的事件转发到 React。

| 方法 | 路径 | 工作流 |
| --- | --- | --- |
| `POST` | `/internal/v1/workflows/exploration-tournament:stream` | 赛前探索策展 |
| `POST` | `/internal/v1/workflows/tournament-insight:stream` | 赛后洞察与推荐 |
| `POST` | `/internal/v1/workflows/taste-profile:stream` | 整体偏好报告 |

请求头：

```text
Authorization: Bearer <AGENT_INTERNAL_SERVICE_TOKEN>
X-Request-Id: <uuid>
X-Caller-Service: java-business-service
X-User-Id: <uuid 或 guest 受控标识>
```

`X-User-Id` 只是 Java 已鉴权上下文的传递字段，不能替代服务认证；Agent 每次读取数据时仍必须把用户范围传给 Java 内部接口复核。

### 4.3 事件协议

```text
event: stage_started
data: {"requestId":"...","stage":"collect_recordings","message":"正在整理可用曲目"}

event: warning
data: {"requestId":"...","code":"KNOWLEDGE_NOT_FOUND","message":"部分背景资料暂不可用"}

event: result
data: {"requestId":"...","workflow":"...","payload":{...}}

event: error
data: {"requestId":"...","code":"MODEL_TIMEOUT","message":"暂时无法完成生成，请稍后重试"}
```

约束：

- 一个请求只能发出一个终态事件：`result` 或 `error`；
- `stage_started` 和 `warning` 只包含用户可见信息，不包含 Prompt、检索评分、原始网页全文或内部思维链；
- `result.payload` 必须通过对应 Pydantic Schema 校验；
- Java 中断下游连接时，Agent 应取消运行并停止后续工具调用；
- 请求超时由 Java 和 Agent 分别控制，且 Java 超时必须大于 Agent 单次工作流超时。

## 5. Pydantic 契约

### 5.1 公共模型

```python
class Evidence(BaseModel):
    kind: Literal["knowledge_claim", "web_source", "user_vote", "music_relation"]
    claim_id: UUID | None = None
    source_url: HttpUrl | None = None
    source_title: str | None = None
    entity_id: UUID | None = None
    supports: str

class AgentWarning(BaseModel):
    code: str
    message: str

class AgentResultBase(BaseModel):
    request_id: UUID
    workflow: str
    warnings: list[AgentWarning] = []
    evidence: list[Evidence] = []
```

### 5.2 探索赛事草稿

```python
class ExplorationTournamentRequest(BaseModel):
    request_id: UUID
    user_id: UUID | None
    artist_id: UUID
    size: Literal[16, 32]
    goal: Literal["discover_entry_direction", "rediscover_artist"]
    exclude_recording_ids: list[UUID] = []
    allow_collaborations: bool = True

class ExplorationTournamentResult(AgentResultBase):
    workflow: Literal["exploration_tournament_planner"]
    status: Literal["ready_for_confirmation", "insufficient_candidates"]
    artist_id: UUID
    size: Literal[16, 32]
    recording_ids: list[UUID]
    curation_summary: str
    coverage: list[dict]
```

只有 `status=ready_for_confirmation` 时，`recording_ids` 数量必须严格等于 `size` 且不重复。

### 5.3 洞察与偏好报告

```python
class Recommendation(BaseModel):
    type: Literal["same_artist_next", "similar_artist", "album_path"]
    target_entity_id: UUID
    reasoning: str
    confidence: Literal["low", "medium", "high"]
    evidence: list[Evidence]

class TournamentInsightResult(AgentResultBase):
    workflow: Literal["tournament_insight_generator"]
    tournament_id: UUID
    tournament_version: int
    scope: Literal["single_tournament"]
    summary: str
    signals: list[dict]
    recommendations: list[Recommendation]

class TasteProfileResult(AgentResultBase):
    workflow: Literal["taste_profile_generator"]
    profile_version: int
    tournament_count: int
    vote_count: int
    summary: str
    dimensions: list[dict]
    recommendations: list[Recommendation]
```

## 6. LangGraph 实现规则

### 6.1 图实例

- 每个工作流独立定义 `StateGraph`、状态模型和终态；
- 图在应用启动时编译，不能为每个请求重新构建；
- 节点必须是无副作用或可安全重试的；写业务数据由 Java 在收到结果后完成；
- 图之间共享工具实现和输出校验器，不共享用户可变状态；
- `request_id` 贯穿图状态、HTTP 日志、追踪和 SSE 事件。

### 6.2 节点要求

| 节点类型 | 允许操作 | 禁止操作 |
| --- | --- | --- |
| 实体/赛事读取节点 | 调用 Java 内部只读 API | 写 MySQL、修改赛事 |
| 知识检索节点 | 查询 Milvus 返回 Claim | 写入未审核网页全文 |
| 网络研究节点 | 查询允许来源、提取受限摘要 | 爬歌词、下载媒体、绕过限制 |
| LLM 节点 | 使用已授权上下文策展/总结 | 生成未验证实体 ID |
| 验证节点 | Schema、实体、引用、安全检查 | 静默修正用户投票或事实 |

### 6.3 重试

- 模型网络错误、短暂 429/5xx：最多一次带抖动的重试；
- Java 内部只读接口临时失败：最多一次重试；
- Pydantic 校验失败：最多一次受限格式修复；
- 实体数量不足、来源不足、权限不足、版本冲突：不重试，直接返回结构化业务错误/降级结果；
- 不进行无限循环或“让模型自行重试直到成功”。

## 7. 工具实现

### 7.1 Java 内部工具客户端

`MusicCatalogTool` 和 `TournamentContextTool` 使用 `httpx.AsyncClient` 调用 Java 内部 API。

必须：

- 传递 `X-Request-Id` 和服务身份；
- 使用显式 timeout、连接池和有限重试；
- 校验 Java 响应的 Pydantic Schema；
- 将上游 `404`、`403`、`409` 映射为明确 Agent 错误；
- 不缓存用户私有赛事快照超过当前请求生命周期。

### 7.2 Milvus 工具

Milvus 通过 `KnowledgeSearchTool` 封装，调用方只能获取：

```text
claim_id, summary, subject_entity_id, source_url,
source_title, source_type, review_status, similarity_score
```

工具层必须在查询时过滤 `review_status`，默认只允许 `REVIEWED`。Milvus 的 Collection、嵌入模型、入库和审核流由知识库规格定义。

### 7.3 网络查询工具

网络搜索实现为 `WebResearchProvider` 协议：

```python
class WebResearchProvider(Protocol):
    async def search(self, query: str, *, source_types: set[str], limit: int) -> list[WebResult]: ...
    async def summarize(self, url: str, *, max_characters: int) -> SourceSummary: ...
```

- 不将具体搜索供应商写死在 Graph 节点；
- API Key 只保存在 Agent 容器环境变量；
- 默认限额：每次工作流最多 3 个搜索查询、最多 5 个来源摘要；
- 命中官方来源时优先使用；
- 临时网页资料默认不写 Milvus，除非单独审核管道批准。

### 7.4 未来知识图谱工具

`RelationGraphTool` 是可选依赖。Neo4j 未启用时返回空关系和 `GRAPH_UNAVAILABLE` 警告，Graph 节点必须继续正常执行。

## 8. 模型 Provider 抽象

```python
class ChatModelProvider(Protocol):
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_input: str,
        schema: type[BaseModel],
        metadata: dict[str, str],
    ) -> BaseModel: ...
```

### 8.1 DeepSeek Provider

- 通过 OpenAI 兼容 Chat Completions 客户端接入；
- API Key、Base URL、模型名称均从环境变量读取；
- 仅向模型发送本次工作流所需的规范实体、Claim 摘要、网页受限摘要和用户信号；
- 不将完整歌词、音频、原始 Provider 响应、其他用户数据或密钥发送给模型；
- 若供应商原生结构化输出不可用，使用严格 JSON 提示词 + Pydantic 校验与一次修复。

### 8.2 Provider 配置

```text
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY=...
LLM_BASE_URL=https://api.deepseek.com
LLM_TIMEOUT_SECONDS=45
LLM_MAX_RETRIES=1
```

切换 Provider 不得修改 API 契约、Graph 状态或 Java 业务逻辑。

## 9. 配置与密钥

`.env.example` 仅包含变量名与示例，不包含真实密钥。

```text
AGENT_INTERNAL_SERVICE_TOKEN=
JAVA_INTERNAL_BASE_URL=http://java-service:8080
LLM_PROVIDER=deepseek
LLM_API_KEY=
MILVUS_URI=http://milvus:19530
WEB_RESEARCH_PROVIDER=
WEB_RESEARCH_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
OTEL_EXPORTER_OTLP_ENDPOINT=
```

启动时：

- `/health/live` 不读取密钥；
- `/health/ready` 校验必需配置与 Java 内部接口；
- 缺失模型密钥时，服务可启动但工作流请求返回 `MODEL_PROVIDER_UNAVAILABLE`；
- 生产环境不得使用默认服务 Token。

## 10. 安全、隐私与内容约束

1. 仅接受来自 Java 服务所在私有网络的请求，并验证服务 Token。
2. 记录用户 ID 时只写不可逆哈希或内部 trace 关联值。
3. 日志与追踪中脱敏 `Authorization`、Cookie、API Key、模型输入中的个人标识。
4. 不持久化完整歌词、媒体二进制、网页全文或模型内部推理。
5. 输出前执行 `SafetyAndEvidenceValidator`：拒绝敏感个人属性推断、无来源事实和未解析实体 ID。
6. 若网络资料不能满足版权或访问限制，只引用链接与短摘要，不复制内容。

## 11. 错误码

| 错误码 | HTTP/SSE 语义 | Java 行为 |
| --- | --- | --- |
| `INVALID_REQUEST` | 输入不符合 Schema | 返回 400，不重试 |
| `UNAUTHORIZED_CALLER` | 服务身份无效 | 返回 401，记录安全事件 |
| `ENTITY_NOT_FOUND` | Java 无法解析艺人/歌曲 | 反馈用户重新选择 |
| `INSUFFICIENT_CANDIDATES` | 无法凑齐 16/32 首合规候选 | 保留草稿，提示改自选/换艺人 |
| `KNOWLEDGE_UNAVAILABLE` | Milvus/网络资料不可用 | 使用降级输出或提示 |
| `MODEL_PROVIDER_UNAVAILABLE` | 模型配置、限流或超时 | 可重试，不改变赛事 |
| `MODEL_OUTPUT_INVALID` | 结构化输出多次校验失败 | 记录追踪，返回重试提示 |
| `FORBIDDEN_CONTEXT` | Agent 无权读取赛事/用户数据 | Java 返回 403 语义 |
| `INTERNAL_TOOL_FAILURE` | 受控工具不可恢复错误 | 返回受控 5xx 语义 |

## 12. 测试策略

### 12.1 单元测试

- Pydantic 请求/结果模型与边界值；
- 候选去重、数量校验、版本冲突校验；
- 引用覆盖与敏感推断拦截；
- DeepSeek Provider 的 JSON 解析与格式修复；
- 每个 Graph 节点在 mock 工具返回下的状态转换。

### 12.2 契约测试

- 与 Java 内部实体、赛事快照、偏好信号 API 的请求/响应契约；
- SSE 事件顺序、唯一终态和取消行为；
- Agent 结果被 Java 拒绝时的错误映射；
- 多模型 Provider 对相同 Pydantic Schema 的兼容性。

### 12.3 集成测试

- 使用 DeepSeek 测试凭据或 Mock Provider 跑通三条 Graph；
- 使用 Milvus 测试 Collection 验证 `REVIEWED` 过滤；
- 模拟 Java 超时、模型 429、网络查询失败并确认降级；
- 验证不存在 MySQL 直连配置和写权限。

## 13. 验收标准

- [ ] FastAPI 只暴露健康检查和内部工作流接口；
- [ ] 所有工作流请求均验证服务 Token、`X-Request-Id` 和 Pydantic 输入；
- [ ] Java 可收到阶段事件与唯一终态 SSE 事件；
- [ ] 所有最终结果通过 Pydantic Schema 校验；
- [ ] Exploration Graph 只返回 Java 提供的 `recording_id`；
- [ ] Agent 不能直接连接 MySQL 或修改赛事；
- [ ] 网络/模型/Milvus 失败不会泄露内部错误或阻断已有赛事；
- [ ] 模型输入、日志、追踪中不含完整歌词、密钥或其他用户私有历史；
- [ ] 默认 DeepSeek 与至少一个 Mock Provider 均能通过契约测试；
- [ ] Docker 健康检查能区分存活与就绪状态。

## 14. 不在本规格范围内

- Milvus Collection Schema、知识文档解析与人工审核后台；
- Web 搜索供应商的最终商业选择与费用；
- Neo4j 数据建模与图谱构建；
- Java 服务内部实现、MySQL 表和 Spring Security 配置；
- React 赛事体验与 SSE 消费实现；
- 生产级异步任务队列。MVP 使用 Java–Agent 的请求级流式调用，批量任务确有需要时再评估队列。
