# Agent 控制面、对话报告与偏好反馈规格

**状态：** 草案
**最后更新：** 2026-08-30
**上级规格：** `11-对话式会话运行时技术规格.md`、`14-并发一致性、异步任务与韧性治理.md`

## 1. 产品定位与统一 Agent

IndieSoundQuest 的主入口是持续的音乐探索对话，而非歌曲世界杯。对外只有一个 **统一音乐探索 Agent**：每次对话均由它理解上下文、规划、调用能力、展示结果并继续对话。歌曲世界杯是 Agent 在用户需要更强偏好信号、希望以淘汰赛方式筛选歌曲时主动提议或由用户启动的可选交互模块。

报告可以在不创建赛事的情况下生成。它使用已授权、可解释的信号：本轮及历史对话中用户明确表达的偏好、用户对推荐的反馈、已完成赛事中的选择事件、受控公开资料与主题卡。报告必须显示信号来源和不足之处；人格内容仅作为娱乐性彩蛋。

旧的“候选池 Agent”“报告 Agent”不再作为两个对用户可见、独立路由的业务 Agent。候选池构建、赛事分析和报告撰写降级为统一 Agent 可自主调用的复合工具；内部仍可作为受控子任务执行，以保留独立预算、重试、证据门槛和 Trace。

## 1.1 Tool 与 Skill 的边界

| 类型 | 定义 | 例子 |
| --- | --- | --- |
| 原子 Tool | 输入输出小、可直接执行的受控能力 | 网络搜索、MusicBrainz 核验、读取赛事事实、写入反馈 |
| 复合 Tool | 由子任务完成、向核心 Agent 返回结构化摘要与证据的能力 | `build_candidate_pool`、`analyze_tournament`、`generate_exploration_report`、`generate_tournament_report` |
| Skill | 目标导向的渐进加载操作手册；声明适用场景、可用工具、质量规则与产物，不规定固定调用顺序 | `song_world_cup`、`open_music_exploration`、`post_tournament_reflection`、`taste_profile_review` |

核心 Agent 按 ReAct 自主决定是否加载某个 Skill、调用哪个 Tool、何时澄清或停止。Skill 绝不能退化为“先 A 再 B 再 C”的固定工作流；运行时护栏只负责权限、预算、契约、无收益循环和用户确认。

## 1.2 Registry

`ToolRegistry` 由代码注册、数据库保存运行策略和状态。Tool 声明名称、能力摘要、输入/输出 Schema、风险、超时、并发、缓存、熔断、证据类型与是否可被报告引用。

`SkillRegistry` 保存 Skill 名称、版本、触发线索、可见工具摘要、质量门槛、用户可见产物类型和禁用条件。第一版在代码中定义并随服务发布，运行状态可观测；不提供后台动态编辑。

复合 Tool 的子任务只能返回：结构化结果、来源/信号引用、可见执行摘要、失败或降级原因。不得向核心 Agent 或前端返回原始 Prompt、思维链、密钥、Cookie 或内部 URL。

## 2. Agent Run 与事件

### 2.1 持久化范围

只保存用户可见或可审计的投影，不保存完整 Prompt、模型思维链、密钥、Cookie、内部地址或完整外部网页正文。

`agent_run`：`id`、`conversation_id`、`tournament_id`（可空）、`run_type`（`CANDIDATE_POOL`/`EXPLORATION_REPORT`/`TOURNAMENT_REPORT`）、状态、模型提供商/模型名、预算摘要、输入版本、开始/结束时间、最终产物引用。

`agent_run_event`：`run_id`、连续序号、`type`、用户可见 payload、时间、traceId。事件类型包括：计划更新、工具摘要开始/结束、澄清请求、降级、重试、结果、失败、取消。

浏览器 SSE 连接首先回放已持久化事件，再订阅实时事件；刷新页面不得丢失已展示轨迹。

### 2.2 状态机

```text
QUEUED → RUNNING → COMPLETED
                 ↘ FAILED
RUNNING → WAITING_FOR_USER → QUEUED
WAITING_FOR_USER → EXPIRED（7 天）
QUEUED/RUNNING/WAITING_FOR_USER → CANCELLED
```

澄清事件是结构化卡片。用户回答后恢复同一 `runId` 和同一上下文快照；过期后可从原对话复制必要摘要启动新 Run。

## 3. Tool Registry

第一版采用“代码注册 + 数据库运行状态/策略”。每个工具声明名称、用途摘要、输入 Schema、输出证据类型、风险级别、超时、并发上限、缓存、熔断和可用状态。

Agent 只能看见当前策略允许的工具能力摘要；Tool Registry 负责执行前策略校验、执行后统一记录工具摘要和证据。首期不提供后台动态增删工具。

## 4. 对话报告与证据

报告 Agent 有两种输入模式：

| 模式 | 信号 |
| --- | --- |
| `EXPLORATION_REPORT` | 对话显式偏好、已确认实体、推荐反馈、长期偏好事件、必要时网络/知识库证据 |
| `TOURNAMENT_REPORT` | 上述信号 + 本场对局、淘汰轨迹、冠军与关键比较 |

每项报告结论都产生 `ReportClaim`：结论文本、置信等级、`signalRefs`、`evidenceRefs`、适用边界。前端“为什么这样说/为什么推荐”只展示这些证据投影。

## 5. 推荐反馈与长期偏好

推荐歌曲/艺人卡提供 `LIKE`、`NEUTRAL`、`DISLIKE`、`ALREADY_KNOWN`、`HIDE_ARTIST`。反馈立即写入 `PreferenceEvent`，并默认参与下一次候选生成和报告；用户可在偏好设置中关闭此参与开关。

反馈永不自动推断敏感属性，也不直接覆盖用户明确的本轮偏好。

## 6. 验收

1. 刷新对话页后，可回放该 Run 的计划、工具摘要、澄清卡与最终结果。
2. 对歧义艺人 Run，用户回答后恢复同一 Run；7 天未答转为 `EXPIRED`。
3. 不创建赛事，仅通过对话与反馈也可生成 `EXPLORATION_REPORT`，且其每条结论具有信号来源。
4. 完成赛事后生成 `TOURNAMENT_REPORT`，结论可引用具体对局事件。
5. 工具不可用时，Run 写入脱敏降级事件，Agent 按策略继续或失败。
6. 对推荐点击反馈后，下一次生成候选池默认读取该偏好事件。
