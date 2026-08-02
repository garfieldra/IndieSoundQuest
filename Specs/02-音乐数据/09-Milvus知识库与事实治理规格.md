# Milvus 知识库与事实治理规格

**状态：** 草案 v0.1  
**最后更新：** 2026-08-01  
**上级规格：** `00-系统总体设计与规格路线图.md`  
**依赖规格：** `01-歌曲信息数据来源与版权边界.md`、`05-Agent工作流规格.md`、`07-Python Agent服务规格.md`

## 1. 目标

为音乐探索 Agent 提供可检索、可引用、可审核的背景事实库。Milvus 负责语义召回；MySQL 中的知识事实与来源记录才是权威事实源。

知识库要解决的是“Agent 如何有依据地说明一位艺人的创作阶段、专辑关系或已公开背景”，不是收集整篇乐评，更不是歌词数据库。

## 2. 设计原则

1. **Claim 优先。** 最小知识单元是带来源的短事实 Claim，不是整篇文章切块。
2. **MySQL 为事实源，Milvus 为索引。** 任何可见结论必须能由 Claim ID 回查到来源。
3. **先审核后召回。** MVP 中 Agent 默认只检索 `REVIEWED` Claim。
4. **摘要而非转载。** 保存必要、短小的事实摘要，不保存歌词全文、完整文章或受版权限制的媒体内容。
5. **实体绑定。** 每条 Claim 关联一个或多个内部音乐实体 UUID；无法定位实体的内容不进入主知识库。
6. **可失效。** 发现来源错误、页面失效或版权风险时，能撤回 Claim 并从 Milvus 删除。

## 3. 范围

### 3.1 纳入 MVP 的知识

- 艺人、专辑、录音版本之间的已验证关系；
- 艺人或唱片公司公开介绍中的创作背景、发行信息、合作信息；
- 正式采访中可简洁转述的公开事实；
- 可信署名音乐媒体中可归因的、与导览直接有关的信息；
- 人工整理的流派、时期、场景关系及其来源。

### 3.2 不纳入 MVP 的知识

- 完整歌词、逐行歌词、歌词向量或歌词问答；
- 付费墙、登录后页面或受 robots 限制内容的抓取；
- 未经许可的音乐平台评论、用户动态、完整乐评转载；
- 未实体消歧的传闻、论坛内容、模型自行生成的“背景”；
- 音频、封面、视频或任何媒体二进制。

## 4. 知识数据模型

### 4.1 MySQL 权威表

```text
knowledge_source
  id, url, title, source_type, publisher,
  published_at, accessed_at, language,
  rights_note, source_status, content_hash,
  created_at, updated_at

knowledge_claim
  id, subject_entity_type, subject_entity_id,
  predicate, object_text, summary,
  source_id, source_locator, claim_type,
  review_status, reviewer_id, reviewed_at,
  effective_from, effective_to, retraction_reason,
  embedding_version, indexed_at, created_at, updated_at

knowledge_claim_entity
  claim_id, entity_type, entity_id, relation_role

knowledge_ingestion_job
  id, source_id, status, pipeline_version,
  started_at, completed_at, error_code, error_detail_redacted
```

### 4.2 字段说明

| 字段 | 说明 |
| --- | --- |
| `summary` | 面向 Agent 的短事实表述，必须保持原意且不复制长原文 |
| `predicate` | 受控谓词，如 `released_on`、`collaborated_with`、`described_as`、`part_of_era` |
| `claim_type` | `FACT`、`EDITORIAL_CONTEXT`、`INTERVIEW_STATEMENT` |
| `source_locator` | 支持该 Claim 的页面段落/时间戳/锚点，不保存原文全文 |
| `review_status` | `DRAFT`、`REVIEWED`、`REJECTED`、`RETRACTED` |
| `rights_note` | 来源允许的使用方式、归属要求和已知限制 |

## 5. 来源分级与审核

| 级别 | 来源类型 | 默认策略 |
| --- | --- | --- |
| A | 艺人官网、唱片公司、发行方、官方音乐节/厂牌 | 可自动提取为 `DRAFT`，人工审核后发布 |
| B | 正式采访、权威奖项/机构、可核实音乐数据库 | 同上，需保留原始链接与日期 |
| C | 有署名且可访问的可信音乐媒体 | 仅提取导览必要事实，人工审核 |
| D | 博客、论坛、社交媒体、未署名转载 | 不进入 MVP 主知识库 |

审核人必须确认：实体是否正确、摘要是否忠实、来源能否支持 Claim、是否涉及受限内容、表述是否将观点误写为事实。

## 6. 入库流程

```mermaid
flowchart LR
  A["登记来源 URL"] --> B["合规抓取/受限摘要"]
  B --> C["实体识别与人工确认"]
  C --> D["提取短事实 Claim"]
  D --> E["审核来源、事实、版权"]
  E --> F["写入 MySQL 权威表"]
  F --> G["生成 Embedding"]
  G --> H["Upsert Milvus 索引"]
  H --> I["Agent 可检索"]
```

### 6.1 规则

- 网络查询工具找到的页面只能进入候选来源池，不能自动进入 Milvus；
- 每次摘要/提取均保留 `pipeline_version`，方便重跑与审计；
- 实体匹配使用 `04-音乐实体模型与跨平台匹配.md` 的内部 UUID；
- 一个来源可产生多个 Claim；一个 Claim 可关联多个实体；
- Claim 被标记为 `RETRACTED` 或来源失效后，必须从 Milvus 删除或过滤；
- 嵌入生成失败不得阻塞 MySQL 中 `REVIEWED` Claim 的保存，但该 Claim 在索引成功前不可被语义检索。

## 7. Milvus 索引设计

### 7.1 Collection

MVP 建立单一 Collection：`music_knowledge_claims`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `claim_id` | VARCHAR，主键 | 对应 MySQL `knowledge_claim.id` |
| `embedding` | FLOAT_VECTOR | `summary` 与必要实体上下文的向量 |
| `subject_entity_id` | VARCHAR | 主要实体 UUID |
| `subject_entity_type` | VARCHAR | Artist/ReleaseGroup/Recording 等 |
| `claim_type` | VARCHAR | 受控 Claim 类型 |
| `review_status` | VARCHAR | 查询时必须过滤为 `REVIEWED` |
| `language` | VARCHAR | 默认 `zh`，后续可扩展 |
| `source_type` | VARCHAR | 来源等级与显示策略 |
| `published_at_ts` | INT64 | 来源发布时间（可空） |
| `embedding_version` | VARCHAR | 嵌入模型/版本 |

`summary` 原文只存 MySQL；Milvus 返回 `claim_id` 和必要标量字段后，再由知识库工具按需读取安全摘要和来源。

### 7.2 嵌入与索引

- 默认使用支持中文语义检索的可替换 `EmbeddingProvider`；初始建议 `BAAI/bge-m3`；
- 向量距离使用 cosine；
- 索引默认 HNSW，具体 `M`、`efConstruction`、`efSearch` 通过环境配置管理；
- 每次变更嵌入模型必须创建新的 `embedding_version`，支持双索引迁移与回滚；
- Agent 查询不得跨版本混合结果，除非迁移策略显式允许。

## 8. 检索契约

Python Agent 只能调用 `KnowledgeSearchTool`：

```python
async def search_verified_knowledge(
    *,
    query: str,
    entity_ids: list[UUID],
    claim_types: list[str] | None,
    top_k: int,
) -> list[VerifiedClaim]: ...
```

检索步骤：

1. 对查询生成 Embedding；
2. 在 Milvus 以 `review_status == REVIEWED` 为强制过滤条件搜索；
3. 有实体上下文时优先过滤/提升关联实体；
4. 去除同一来源、同一谓词的高度重复 Claim；
5. 根据 `claim_id` 从 MySQL 读取摘要、URL、来源标题、审核状态；
6. 返回最多 `top_k` 条 Claim，默认 `top_k <= 8`。

返回给 LLM 的每条 Claim 必须有：

```json
{
  "claimId": "uuid",
  "summary": "…",
  "subjectEntityId": "uuid",
  "sourceUrl": "https://…",
  "sourceTitle": "…",
  "sourceType": "official",
  "reviewStatus": "REVIEWED"
}
```

## 9. 召回质量与降级

### 9.1 质量规则

- 低相似度结果不能单独支撑用户可见事实；
- 同一事实至少由一个审核来源支持即可展示，但多来源可提高置信度；
- 对“观点/风格评价”类 Claim 显示来源属性，例如“某媒体评价为…”，不能改写为客观事实；
- Agent 需区分 Claim 支撑的结论与基于用户投票的推荐推断。

### 9.2 降级

| 场景 | 行为 |
| --- | --- |
| Milvus 不可用 | Agent 跳过知识库，使用实体元数据和提示语降低解释强度 |
| 没有相关 REVIEWED Claim | 不调用网络来源编造结论；可给出基于曲目/时期的路线 |
| MySQL Claim 已撤回 | 立即在返回层过滤，并触发 Milvus 删除任务 |
| 嵌入模型切换中 | 使用已完成索引的稳定版本，不混用未完成版本 |

## 10. 运维与权限

- Milvus 仅在 Docker 内部网络暴露，不开放给浏览器；
- 只有 Agent 服务和受控入库任务拥有 Milvus 凭据；
- Java 不直接执行向量查询；Java 只保存/展示 Agent 返回的来源引用；
- 入库任务密钥与模型 API Key 通过环境变量/密钥管理注入；
- 定期核验 `REVIEWED` Claim 的来源可访问性，失效不等于事实必然错误，但需标记复核；
- MySQL 与 Milvus 的索引一致性通过 `indexed_at`、`embedding_version` 和重建任务监控。

## 11. 评估与验收

### 11.1 离线测试集

维护小规模人工标注测试集，覆盖：

- 艺人创作阶段问题；
- 专辑与歌曲关系问题；
- 合作/制作人关系问题；
- 中文同名实体消歧；
- 来源不足、无法回答的负例；
- 观点型资料与事实型资料的区分。

### 11.2 指标

| 指标 | 目标 |
| --- | --- |
| Claim 来源完整率 | 100% |
| REVIEWED 过滤正确率 | 100% |
| 检索结果实体相关率 | 通过人工集持续评估 |
| 事实引用可打开率 | 持续监控 |
| 撤回 Claim 索引清理延迟 | 可观测且可重试 |
| Agent 无依据事实率 | 趋近 0 |

### 11.3 验收项

- [ ] 未审核、拒绝、撤回 Claim 不会被 Agent 检索；
- [ ] 每个可返回 Claim 都可回查 MySQL 来源记录；
- [ ] Claim 与至少一个内部音乐实体关联；
- [ ] Collection 不包含歌词、文章全文、音频或封面二进制；
- [ ] Milvus 不可用时，探索赛事仍能创建并明确降级；
- [ ] 删除/撤回 Claim 后，后续检索不会再返回该 Claim；
- [ ] 嵌入模型版本可识别、可重建、可回滚；
- [ ] Agent 输出的关键事实可显示来源链接或“基于用户选择”的明确标识。

## 12. 不在本规格范围内

- Neo4j 知识图谱模型与多跳检索；
- 具体网页搜索供应商、爬虫实现或浏览器自动化；
- 完整 CMS/人工审核后台的页面设计；
- Milvus、MySQL 的 Docker 参数和备份策略；
- LLM 生成事实摘要的具体 Prompt；
- 歌词数据授权与接入。
