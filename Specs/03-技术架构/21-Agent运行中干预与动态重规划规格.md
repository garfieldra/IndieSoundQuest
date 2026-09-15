# Agent 运行中干预与动态重规划规格

状态：已实现并验收

## 1. 目标

用户在统一音乐 Agent 运行期间仍可继续输入，但默认语义是“排队成为下一轮消息”，而不是立即改变正在生成的结果。只有用户显式点击“立即介入本轮”，排队消息才会在下一个安全动作边界注入当前 ReAct 状态并触发计划修订。两种状态都必须可靠持久化。

本功能展示的是可公开执行推理：目标理解、计划、工具摘要、证据缺口和调整原因。不得输出模型隐藏思维链、系统提示词、密钥、Cookie、内部地址或未经清洗的工具响应。

## 2. 首版范围

- 支持 `CONVERSATION` 与 `EXPLORATION_REPORT` Agent Run。
- 运行中的输入框保持可用；Enter 默认把消息加入下一轮队列，Shift+Enter 换行。
- 每个 Run 同一时间最多存在一条待发送消息；存在待发送消息或本轮已经介入后，输入区锁定到本轮结束。
- 用户可以把唯一一条待发送消息显式转换为本轮 Intervention；每个 Run 最多允许一次 Intervention。
- 当前输入为空且 Run 正在运行时，发送按钮原位切换为停止按钮；不再在过程卡中重复提供主要停止入口。
- 指令、幂等键、顺序和应用状态由 MySQL 持久化。
- Worker 在统一 ReAct Agent 每次 Supervisor 决策前检查新指令。
- 新指令进入现有上下文，保留已经取得的有效证据，同时使未执行计划重新评估。
- 发布 `INTERVENTION_ACCEPTED`、`INTERVENTION_APPLIED` 和新版 `PLAN_UPDATED` 事件。
- Worker 重启后按序重放该 Run 的全部指令，不因已经标记 APPLIED 而遗失。
- 首版不强制中断正在进行的 HTTP 请求；输入在当前工具返回后的安全边界生效。
- 候选池独立任务、赛事报告独立任务和沙箱/任意 CLI 执行不属于首版范围。

## 3. 用户交互

当 Agent 正在运行时，输入框允许用户准备下一条消息。直接提交后：

1. 消息写入 `agent_run_follow_up`，但尚不写入正式对话时间线；
2. 输入区展示唯一的等待消息及“立即介入本轮”按钮，并禁止继续排队；
3. 当前 Run 完成、失败或被用户停止后，Java 在同一事务中将等待消息写入对话并创建下一 Agent Run；
4. 页面自动接续下一 Run，不要求用户再次点击发送；
5. 若点击“立即介入本轮”，等待项原子转换为 Intervention、写入当前对话，并在下一 Supervisor 安全边界生效。

最终结果出现后，中间执行过程仍按现有规则折叠并可回看。

## 4. 数据模型

`agent_run_intervention`：`id`、`run_id`、`conversation_id`、`guest_session_id`、`client_message_id`、`sequence_number`、`type`、`content`、`status`、`created_at`、`applied_at`。

- 首版 `type` 固定为 `ADJUST_DIRECTION`。
- 状态为 `PENDING | APPLIED | SUPERSEDED`。
- 唯一约束为 `(run_id, client_message_id)` 与 `(run_id, sequence_number)`；首版额外限制每个 Run 最多一条 Intervention。

`agent_run_follow_up`：`id`、`run_id`、`conversation_id`、`guest_session_id`、`client_message_id`、`content`、`status`、`next_run_id`、`created_at`、`resolved_at`。

- 状态为 `WAITING | DISPATCHED | INTERVENED | CANCELLED`。
- `run_id` 唯一，保证同一运行只有一个等待区；`(conversation_id, client_message_id)` 唯一，保证提交幂等。
- `DISPATCHED.next_run_id` 用于页面刷新、SSE 重连和 Worker 重试后继续追踪下一轮。

用户消息同时写入 `conversation_message`，因此页面刷新后不会消失。Agent 最终回答使用当前对话的最新可用序号写入，不能再假设回答永远位于原始 `AGENT_RUN` 占位消息之后一位。

## 5. API

### 5.1 用户提交调整

```http
POST /api/v1/agent-runs/{runId}/interventions
Idempotency-Key: UUID
Content-Type: application/json

{"content":"不要再扩展歌词，重点核对制作人与录音版本。"}
```

仅 Run 所有者可提交；Run 必须为 `QUEUED` 或 `RUNNING`，并且属于支持的对话类 Run。

### 5.2 Worker 拉取与确认

Worker 使用内部令牌和有效 Lease 获取 `sequenceNumber > afterSequence` 的指令。查询包含已经 APPLIED 的历史项，使 Worker 重启后能够从零确定性重放。Worker 在指令交给 Supervisor 前确认已接收，Java 标记对应项并写入公开事件；事件只记录序号、数量和安全摘要。

### 5.3 排队下一条消息

`POST /api/v1/agent-runs/{runId}/next-message` 创建或幂等返回唯一等待项；`GET` 恢复等待/分发状态；`POST /api/v1/agent-runs/{runId}/next-message:intervene` 将它原子转换为本轮 Intervention。第二条不同消息返回 `409`，不得覆盖第一条。

## 6. ReAct 语义

- 当前用户消息是初始目标；Intervention 是更高时效性的追加约束，冲突时以最新项为准。
- 新指令不得清空已经取得的来源，但 Supervisor 可以判断其与新目标无关并停止继续使用。
- 每次收到新指令重置本轮决策预算，为重新研究保留有限动作空间。
- 实体纠正覆盖旧实体假设；新增信息进入近期上下文；排除项影响后续推荐和候选选择。
- Agent 每轮仍自主选择一个动作，不引入固定业务工作流。

## 7. 一致性与恢复

- 提交接口使用 Agent Run 行锁分配 Intervention 顺序。
- 幂等重试返回同一 Intervention，不重复写消息。
- Run 完成与排队/介入竞争时，以 Run 行锁决定结果；终态 Run 拒绝新的排队与介入。
- 当前 Run 结果、正式 Assistant 消息、等待消息分发和下一 Run Outbox 创建处于同一事务；服务重启不会形成“消息已消失但下一轮未创建”的半完成状态。
- Worker 使用 `appliedInterventionSequence` 去重；Worker 重试则从序号 0 重放，保证指令不会因进程崩溃丢失。
- 最终 trace 记录最后应用序号，便于验收和审计。

## 8. 验收标准

1. 运行中直接发送消息只进入等待区，不改变当前 Run；当前 Run 终态后自动创建并跟随下一 Run。
2. 同一 Run 第二条等待消息返回冲突；相同 Idempotency-Key 重试不重复创建。
3. 用户显式点击后，等待消息转换为唯一 Intervention，并在下一次 Supervisor 决策前应用、发布新版公开计划。
4. 本轮已经介入后不得再次排队或介入；trace 只包含最后应用序号，不包含隐藏思维链。
5. 页面刷新、SSE 重连、Worker 重试不会丢失等待项或重复分发下一 Run。
6. 输入为空时主按钮停止当前 Run；有文字时主按钮发送到等待区。
7. Run 已等待澄清或进入其他终态后拒绝新的排队与介入。
8. Python、Java、前端测试、Docker 构建和真实端到端排队/介入验收通过。

## 9. 验收记录

- Agent 测试：84 项通过，其中覆盖 Supervisor 注入、邮箱异常不可静默跳过、公开播报清洗与回答无损分块。
- Java 测试：28 项通过；Flyway `V21` 已在 MySQL 8.4 实际执行。
- 前端测试：26 项通过，TypeScript 与 Vite 生产构建通过。
- 真实端到端排队链路通过：等待消息幂等、第二条返回 `409`、当前 Run 完成后自动创建并完成下一 Run，时间线无丢失或重复。
- 真实端到端介入链路通过：等待消息原子转换为唯一 Intervention、重复转换幂等、第二条等待消息与终态介入均返回 `409`。
- 最终 `traceSummary.appliedInterventionSequence=1`；公开事件包含排队、转换、应用和计划修订状态，未写入隐藏思维链。

## 10. Codex 风格公开过程与增量回答

- `COMMENTARY` 事件使用动作、计数和证据状态生成自然语言阶段播报，只描述当前发现、证据缺口和下一步意图，不复制模型草稿或工具原文。
- `RESPONSE_STARTED` 与 `RESPONSE_DELTA` 通过既有耐久化 SSE 通道传输；断线重连可按事件序号重建当前回答。
- 对话区实时拼接回答并显示轻量光标，正式 `AGENT_TEXT` 落库后替换临时内容；右侧仍只保留计划 revision。
- 当前采用“结构化结果完成 Schema 校验后再分块交付”。这避免把不完整 JSON、未经审校的事实或随后会被丢弃的草稿暴露给用户，同时提供稳定的增量阅读体验。
- 完成、重试和恢复必须以 `RESPONSE_STARTED` 重置临时文本；所有分块拼接后必须与最终持久化回答逐字一致。
