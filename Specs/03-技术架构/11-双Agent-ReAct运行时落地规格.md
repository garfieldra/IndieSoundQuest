# 双 Agent ReAct 运行时落地规格

## 1. 目标

系统只保留两个业务 Agent：候选池生成 Agent 与赛后报告 Agent。二者共享 Supervisor ReAct Runtime、类型化黑板、工具预算、证据注册表和 Critic，但使用不同的动作白名单与输出契约。

现有固定 LangGraph 链路属于迁移前实现；完成本规格后，可选工具由 Supervisor 根据 Observation 动态选择，不再无条件按预设顺序调用。

## 2. ReAct 工程定义

每轮 Supervisor 读取黑板并输出一个结构化 `Decision`：

```json
{"action":"call_tool","target":"musicbrainz_entity_resolver","reasonCode":"verified_candidates_below_target","arguments":{"queryRef":"expanded-query-2"}}
```

运行时只接受白名单动作与 Pydantic 参数，不保存或展示隐藏思维链。工具返回结构化 Observation；Supervisor 可以继续、调整计划、提交 Critic 或结束。达到预算、超时或连续无进展时强制降级。

## 3. 候选池 Agent

目标总量固定为赛事规模两倍：16 首赛事生成 32 首候选，32 首赛事生成 64 首候选；前半为正式位，后半为候补。

动作白名单：`understand_preference`、`search_local_catalog`、`search_knowledge`、`search_web`、`resolve_musicbrainz_entities`、`import_verified_entities`、`rerank_candidates`、`validate_candidate_pool`、`submit_candidate_pool`。

本地目录数量或多样性不足时，Supervisor 扩展相近艺人、风格、场景与别名；必要时用 Tavily 发现线索，再用 MusicBrainz 搜索、消歧并交给 Java 幂等入库。正式位不足才失败；正式位足够但候补不足时允许带警告降级。

## 4. 报告 Agent

动作白名单：`read_tournament_facts`、`analyze_preference_signals`、`search_knowledge`、`search_web`、`validate_recommendations`、`draft_report`、`critic_review`、`repair_report`、`submit_report`。

Supervisor 可依据本场艺人集中度、证据缺口和推荐覆盖决定是否搜索网络。赛事事实读取、结构校验和 Critic 是不可跳过的安全步骤。报告和推荐仍由同一个报告 Agent 完成，不新增第三个 Agent。

## 5. MusicBrainz 回填链路

```text
Tavily discovery_hint
  → MusicBrainz recording Search
  → 标题/艺人/发行信息消歧
  → Java resolve-and-import（MBID 幂等）
  → 可选 Cover Art Archive 封面
  → 本地 Recording UUID
  → 候选池验证
```

Search 分数不能单独作为入库依据，必须核对规范化标题与艺人署名；模糊匹配需通过二次 Lookup/Browse，否则返回 `ambiguous`。MusicBrainz 请求必须带可联系的 User-Agent，由共享限流器控制为平均每秒不超过 1 次，并配置缓存、503 退避和熔断。

## 6. 验收标准

- [ ] 两个 Agent 均由 Supervisor 根据 Observation 动态选择下一动作；
- [ ] 固定安全步骤保留，但可选工具不再按固定顺序无条件调用；
- [ ] 16/32 首赛事分别以 32/64 个已验证候选为目标；
- [ ] Tavily 发现的目录外歌曲必须经 MusicBrainz 与 Java 本地 UUID 转换；
- [ ] 相近方向扩展受预算与去重约束，正式位不足时才报告失败；
- [ ] 输出经过确定性校验与独立 Critic；
- [ ] 不新增第三个业务 Agent，不暴露模型思维链。
