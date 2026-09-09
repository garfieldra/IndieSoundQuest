# 异步 AgentRun、Worker 与可恢复事件流规格

> 状态：已实现并验收

> 2026-09-08 实施记录：统一对话、候选池、赛后报告和非赛事探索报告均已完成 `202 + runId`、事务 Outbox、统一 RabbitMQ Python Worker、Attempt 租约/心跳、最多三次自动重试、租约过期接管、publisher confirm、Agent DLQ、事件重放、并发幂等与 Micrometer 指标。`tournament_preference_report` 仅保存报告业务制品，执行状态统一由 `AgentRun` 控制面管理。前端采用“提交任务 -> EventSource 订阅持久事件 -> 读取业务制品”，SSE 为主协议，REST 增量读取仅作断线降级；旧报告专用消费者和队列已经移除。

> 同日验收：实际 DeepSeek + MusicBrainz 候选任务生成 32 条可核验结果（16 主池 + 16 补位），随后完成 15 次投票与统一 AgentRun 赛后报告生成。人工停止报告 Worker 后，租约过期任务自动从 `RUNNING` 恢复为 `QUEUED`，Worker 重启后以第 2 次 Attempt 完成，最终只有一份 READY 报告和一个 RESULT 事件。升级 SSE 主协议后再次完成普通对话、候选池、赛后报告、非赛事探索报告四类真实验收；`afterSequence` 与 `Last-Event-ID` 均能准确续传。Python 29 项、Java 20 项、Web 21 项测试通过；Prometheus 已成功抓取 Java 与 RabbitMQ，Grafana 已自动装载 `IndieSoundQuest Agent Control Plane` 看板。

## 1. 目标

将候选池生成、对话分析和报告生成从一次长时间 HTTP 请求中解耦，建模为可持久化、可追踪、可恢复的 `AgentRun`。页面刷新、网络短暂中断或 Worker 重启时，任务与已产生的可展示进度不得丢失。

本规格只改造执行控制面，不将 ReAct Agent 改成固定工作流。Agent 仍自主决定计划、搜索与工具调用。

## 2. 适用范围

首期纳入异步执行：

- 统一对话 Agent，包括直接回答、候选池构建与对话报告。
- 歌曲世界杯赛后报告。
- 需要网络搜索、MusicBrainz 核验或多轮工具调用的长任务。

暂不纳入：查询会话、查询赛事、投票等短事务 API。

## 3. 系统边界

```text
React
  | POST 创建任务 / GET-SSE 订阅事件
  v
Java API
  |-- MySQL: AgentRun / Attempt / Event / Outbox / 业务结果
  |-- Redis: 限流、短期互斥、热点状态
  `-- RabbitMQ: 任务交付、削峰、重试和死信
          |
          v
Python Agent Worker
  |-- ReAct / Skills / Tools / Sub-agents
  `-- 回写可展示事件、心跳和最终结果
```

Java 是任务与业务数据的唯一事实来源。RabbitMQ 不是状态库，Redis 不是最终事实库，Python 不直接修改业务表。

## 4. 接口语义

### 4.1 创建任务

`POST /api/v1/conversations/{conversationId}/messages`

- 必须携带 `Idempotency-Key`。
- Java 在同一数据库事务中写入用户消息、`AgentRun(QUEUED)` 和 Outbox 事件。
- 立即返回 `202 Accepted`，响应含 `runId`、`status`、`eventsUrl`。
- 相同客人、相同幂等键不得创建第二个 Run。

迁移期保留现有 `messages:stream`，但前端不再依赖它执行长任务。

### 4.2 订阅与重放

`GET /api/v1/agent-runs/{runId}/events:stream`

- 支持 `Last-Event-ID`或 `afterSequence`。
- 建立连接后先从 MySQL 重放未接收事件，再继续推送新事件。
- 定期输出心跳，防止反向代理误判空闲。
- Run 进入终态后发送终态事件并结束连接。
- `GET /api/v1/agent-runs/{runId}/events` 作为轮询降级和排障接口。
- 浏览器优先使用 `EventSource`；以事件 `id`/`sequence` 保存游标并自动重连两次，仍失败才使用 REST 增量恢复。
- SSE 响应禁用代理缓冲、缓存和转换；终态由独立 `run_status` 事件明确表达。

## 5. AgentRun 状态机

```text
QUEUED -> RUNNING -> WAITING_FOR_USER -> QUEUED
   |          |             |
   |          +-----------> COMPLETED
   |          +-----------> FAILED
   |          +-----------> CANCELLED
   +----------------------> EXPIRED
```

- 对一次 SSE 订阅而言，`COMPLETED / WAITING_FOR_USER / FAILED / CANCELLED / EXPIRED` 均结束本次连接；其中 `WAITING_FOR_USER` 是可恢复的业务暂停态而非最终失败。
- 状态转移必须由 Java 校验，Worker 只能提交转移请求。
- 终态不得被普通重试改写。
- 澄清恢复产生新 Attempt，但沿用原 `runId`。

## 6. Attempt、租约与崩溃恢复

`agent_run_attempt` 至少保存：`attemptNo`、`workerId`、`leaseToken`、`status`、`startedAt`、`heartbeatAt`、`finishedAt`、`failureCode`。

- Worker 消费消息后必须向 Java 申领租约；未获得租约时不执行。
- 运行期每 10 秒续租，默认租约 30 秒。
- 每次回写都携带 `attemptNo + leaseToken`，Java 拒绝过期 Worker 的迟到结果（fencing token）。
- 租约过期后 Run 可重新入队；同一时刻只有一个有效 Attempt。

## 7. Outbox 与消息协议

- 业务变更和 Outbox 必须同事务提交。
- Publisher 使用 RabbitMQ publisher confirm；收到 broker 确认后才标记 `PUBLISHED`。
- 消息包含 `messageId`、`runId`、`runType`、`inputVersion`、`traceId`和最小必要输入。
- 消费端手动 ACK：只有最终结果或 `WAITING_FOR_USER` 已被 Java 接受后才 ACK。
- 投递语义为 at-least-once；通过租约和幂等回写实现业务上 exactly-once effect。
- 重试使用指数退避加抖动；超过上限进入 DLQ，同时将 Run 标记为可解释的 `FAILED`。

当前首版使用统一耐久队列 `isq.agent-runs.v1`，由消息中的 `runType` 分派到相应 ReAct 图。达到需要独立扩缩容的流量后，再按以下负载类别拆分路由，不能在缺少容量数据时预先复制执行控制面：

- `agent.conversation`：普通对话，优先级高。
- `agent.discovery`：候选池与多源搜索。
- `agent.report`：赛后/探索报告。

## 8. 可持久事件

仅保存与展示可观测性有关的事件，不保存或输出模型思维链：

- `RUN_QUEUED / RUN_STARTED`
- `PLAN_UPDATED`：可展示滚动计划。
- `TOOL_STARTED / TOOL_COMPLETED / TOOL_DEGRADED`：工具名、摘要、耗时与数量，不含密钥和原始 prompt。
- `PROGRESS`：用户可理解的阶段摘要。
- `CLARIFICATION_REQUESTED`
- `RUN_RETRYING / RUN_DEGRADED`
- `RESULT / FAILED / CANCELLED`

事件序号在单个 Run 内严格递增。使用数据库原子更新分配序号，不采用“先查最大值再加一”的竞态实现。

## 9. 并发与容量

- Java API 保持无状态，可水平扩容。
- Python API 与 Worker 使用同一镜像、不同启动命令；Worker 可独立扩容。
- Worker 使用 async I/O，并为 DeepSeek、搜索、MusicBrainz 和国内内容源分别设置 semaphore、超时、重试与熔断。
- RabbitMQ prefetch 与单 Worker 并发数明确配置，不用无上限线程池。
- 扩容依据是队列深度、最旧消息等待时间、P95 耗时和 Provider 限流率，不只看 CPU。

## 10. 失败分类

- `RETRYABLE_PROVIDER_ERROR`：网络超时、5xx、可恢复限流。
- `NON_RETRYABLE_INPUT_ERROR`：输入或 Schema 不合法。
- `INSUFFICIENT_EVIDENCE`：线上发现与核验后仍不足，返回可理解的降级结果。
- `LEASE_LOST`：Worker 丧失执行权，必须立即停止回写。
- `CONTRACT_REJECTED`：Agent 结果未通过 Java Schema/业务校验。

对用户的错误文案不暴露内部地址、密钥、栈追踪或模型原始思考。

## 11. 可观测性

全链路传递 `traceId / runId / conversationId / messageId / attemptNo`。至少采集：

- 各类 Run 的完成率、失败率和 P50/P95/P99 耗时。
- 队列深度、排队时间、重试数、DLQ 数量和租约丢失次数。
- 各 Provider/工具的成功率、耗时、限流和降级原因。
- 候选池完成率、MusicBrainz 核验率、模型 token/成本估算。

## 12. 安全

- API Key 仅存于服务端 Secret/环境变量，不进入消息、AgentRun 事件、SSE、日志或模型上下文。
- Worker 回调 Java 使用内部服务身份，并校验租约 token。
- 事件 payload 实施字段白名单和大小限制。
- 用户只能读取当前访客会话所属 Run。

## 13. 渐进迁移

1. 补齐 `AgentRunAttempt`、租约、原子事件序号和通用 Agent Outbox。
2. 增加 Python Worker 启动入口和 Java 内部回写协议。
3. 先迁移统一对话 Agent，保留旧 SSE 路径用于回退。
4. 前端改为“创建 Run -> 按 runId 订阅”，实现刷新恢复。
5. 迁移候选池与报告，移除报告专用队列、消费者及 Java 到 Python 的同步长 HTTP 执行路径。

## 14. 验收标准

- 创建任务在 500 ms 内返回 `202 + runId`，不等待模型。
- 相同幂等键并发请求只有一个 Run 和一条有效结果。
- 刷新页面后能重放计划、工具摘要和最终结果。
- 人工终止一个 Worker 后，租约过期并重试，不产生两份助手消息或报告。
- RabbitMQ 短暂不可用时，Run 与 Outbox 仍保存；恢复后自动投递。
- 单个外部内容源故障时，Agent 可自主改用其他工具或给出明确降级结果。
- 日志、RabbitMQ 消息、SSE 和数据库事件中不出现 API Key 或思维链。
- 现有投票、候选确认、赛事和报告业务回归通过。

## 15. 非目标

- 首期不引入 Temporal、Kafka 或 Kubernetes。
- 不把 RabbitMQ 当作事件溯源数据库。
- 不向用户展示模型隐藏思维链。
- 不为了并发无限增加线程或工具子 Agent。
