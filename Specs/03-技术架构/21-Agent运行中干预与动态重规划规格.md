# Agent 运行中干预与动态重规划规格

状态：已实现并验收

## 1. 目标

用户在统一音乐 Agent 运行期间仍可继续输入，用新的信息纠正实体、补充背景或改变研究重点。输入应可靠持久化，并在下一个安全动作边界注入当前 ReAct 状态，触发计划修订，而不是创建另一个业务 Agent 或重新开始一段无关对话。

本功能展示的是可公开执行推理：目标理解、计划、工具摘要、证据缺口和调整原因。不得输出模型隐藏思维链、系统提示词、密钥、Cookie、内部地址或未经清洗的工具响应。

## 2. 首版范围

- 支持 `CONVERSATION` 与 `EXPLORATION_REPORT` Agent Run。
- 运行中的输入框保持可用；Enter 提交方向调整，Shift+Enter 换行。
- 指令、幂等键、顺序和应用状态由 MySQL 持久化。
- Worker 在统一 ReAct Agent 每次 Supervisor 决策前检查新指令。
- 新指令进入现有上下文，保留已经取得的有效证据，同时使未执行计划重新评估。
- 发布 `INTERVENTION_ACCEPTED`、`INTERVENTION_APPLIED` 和新版 `PLAN_UPDATED` 事件。
- Worker 重启后按序重放该 Run 的全部指令，不因已经标记 APPLIED 而遗失。
- 首版不强制中断正在进行的 HTTP 请求；输入在当前工具返回后的安全边界生效。
- 候选池独立任务、赛事报告独立任务和沙箱/任意 CLI 执行不属于首版范围。

## 3. 用户交互

当 Agent 正在运行时，输入框提示用户可以补充或调整方向。提交内容后：

1. 立即显示为当前对话中的用户消息；
2. 服务端返回“调整已收到”；
3. 实时执行区展示“正在根据你的补充调整计划”；
4. 右侧计划生成新 revision，被新指令取代的待执行步骤由新计划替换；
5. 最终回答必须反映最新已应用的指令序号。

最终结果出现后，中间执行过程仍按现有规则折叠并可回看。

## 4. 数据模型

`agent_run_intervention`：`id`、`run_id`、`conversation_id`、`guest_session_id`、`client_message_id`、`sequence_number`、`type`、`content`、`status`、`created_at`、`applied_at`。

- 首版 `type` 固定为 `ADJUST_DIRECTION`。
- 状态为 `PENDING | APPLIED | SUPERSEDED`。
- 唯一约束为 `(run_id, client_message_id)` 与 `(run_id, sequence_number)`。

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

## 6. ReAct 语义

- 当前用户消息是初始目标；Intervention 是更高时效性的追加约束，冲突时以最新项为准。
- 新指令不得清空已经取得的来源，但 Supervisor 可以判断其与新目标无关并停止继续使用。
- 每次收到新指令重置本轮决策预算，为重新研究保留有限动作空间。
- 实体纠正覆盖旧实体假设；新增信息进入近期上下文；排除项影响后续推荐和候选选择。
- Agent 每轮仍自主选择一个动作，不引入固定业务工作流。

## 7. 一致性与恢复

- 提交接口使用 Agent Run 行锁分配 Intervention 顺序。
- 幂等重试返回同一 Intervention，不重复写消息。
- Run 完成与新指令提交竞争时，以 Run 行锁决定结果；终态 Run 拒绝新指令。
- Worker 使用 `appliedInterventionSequence` 去重；Worker 重试则从序号 0 重放，保证指令不会因进程崩溃丢失。
- 最终 trace 记录最后应用序号，便于验收和审计。

## 8. 验收标准

1. 运行中连续提交两条调整，两条消息均持久化且顺序稳定。
2. 相同 Idempotency-Key 重试不会产生重复指令或重复消息。
3. Agent 在下一次 Supervisor 决策前应用调整，并发布新版公开计划。
4. 最终回答与最后一条调整一致，trace 包含最后应用序号但不包含隐藏思维链。
5. 页面刷新、SSE 重连和 Worker 重试不会丢失已提交调整。
6. Run 完成、失败、取消或等待澄清时拒绝普通运行中调整。
7. Python、Java、前端测试、Docker 构建和真实端到端干预验收通过。

## 9. 验收记录

- Agent 测试：81 项通过，其中覆盖 Supervisor 注入、邮箱异常不可静默跳过、公开播报清洗与回答无损分块。
- Java 测试：26 项通过；Flyway `V20` 已在 MySQL 8.4 实际执行。
- 前端测试：25 项通过，TypeScript 与 Vite 生产构建通过。
- 真实端到端测试连续两次通过：运行中按序接收两条调整、幂等重放不重复、发布应用与计划修订事件、最终结果采用最新调整。
- 最终 `traceSummary.appliedInterventionSequence` 与最新指令序号一致；公开事件未写入隐藏思维链。

## 10. Codex 风格公开过程与增量回答

- `COMMENTARY` 事件使用动作、计数和证据状态生成自然语言阶段播报，只描述当前发现、证据缺口和下一步意图，不复制模型草稿或工具原文。
- `RESPONSE_STARTED` 与 `RESPONSE_DELTA` 通过既有耐久化 SSE 通道传输；断线重连可按事件序号重建当前回答。
- 对话区实时拼接回答并显示轻量光标，正式 `AGENT_TEXT` 落库后替换临时内容；右侧仍只保留计划 revision。
- 当前采用“结构化结果完成 Schema 校验后再分块交付”。这避免把不完整 JSON、未经审校的事实或随后会被丢弃的草稿暴露给用户，同时提供稳定的增量阅读体验。
- 完成、重试和恢复必须以 `RESPONSE_STARTED` 重置临时文本；所有分块拼接后必须与最终持久化回答逐字一致。
