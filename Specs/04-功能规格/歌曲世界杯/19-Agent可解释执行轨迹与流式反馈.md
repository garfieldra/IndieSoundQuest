# Agent 可解释执行轨迹与流式反馈

**状态：** 已实现、已验证  
**适用范围：** 候选歌曲池 Agent、赛后报告 Agent

## 1. 目标

在 Agent 执行期间向用户实时展示可理解的工作进展，避免无反馈等待；最终结果到达后过程自动折叠，用户仍可主动展开回看。

这不是暴露模型原始思维链。系统只输出经过白名单过滤的“执行轨迹”：正在做什么、为何需要该步骤、可安全公开的阶段性数量与来源标题。

## 2. SSE 事件契约

前端先用 `POST` 创建持久化 `AgentRun`，收到 `202 + runId` 后，通过浏览器原生 `EventSource` 订阅 Java 的 `GET /api/v1/agent-runs/{runId}/events:stream`。Java 是唯一公开入口，也是事件与最终业务结果的事实来源；Python Worker 只能通过受保护的内部接口回写白名单事件。

SSE 是所有长耗时 Agent 操作的主协议，包括候选池、普通对话、赛后报告和非赛事探索报告。`GET /events` 只用于断线多次重连失败后的降级恢复和排障，不作为正常轮询方案。

```text
event: run_event
id: 12
data: {
  "sequence":12,
  "eventType":"TOOL_COMPLETED",
  "payload":{
    "toolName":"search_web",
    "status":"completed",
    "message":"已完成公开音乐资料检索",
    "durationMs":12500,
    "metrics":{"sourceCount":12}
  }
}

event: run_status
data: {"status":"COMPLETED","lastSequence":18}
```

持久事件包括 `PLAN_UPDATED`、`PROGRESS`、`TOOL_STARTED`、`TOOL_COMPLETED`、`TOOL_DEGRADED`、`RESULT` 等。错误仅包含用户可读错误码与说明；来源最多三项，仅输出公开标题和 URL，不输出网页全文。

每条持久事件拥有严格递增的 `sequence`，同时写入 SSE `id`。浏览器重连携带 `afterSequence`，服务端也接受 `Last-Event-ID`，先从 MySQL 重放遗漏事件，再继续等待实时事件。连续两次 SSE 重连仍失败时才启用 REST 增量读取，并展示“正在恢复连接”，不得暴露 `Load failed` 等浏览器原始错误。

## 3. 白名单与安全边界

- 允许阶段：理解偏好、艺人核验、网络发现、MusicBrainz 核验、知识库补充、报告起草、推荐审查、完成。
- 允许指标：已发现来源数、线索数、已核验歌曲数、报告对局数、已用时。
- 禁止：Prompt、原始 CoT、模型内部评分、密钥、请求头、未过滤网页摘要、数据库内部异常栈。
- 发生降级时显示原因类别，例如“网络资料暂不可用，正使用已核验目录继续”。

## 4. 前端体验

- 请求开始后，将计划与时间线直接显示在对应的触发按钮下方：候选池在“生成候选歌曲”下方，报告在“生成本场偏好报告”下方；不得固定插入页面顶部。
- 网络阶段可展开查看最多三条来源链接。
- 收到 `result` 后自动折叠为“本次探索过程（N 步 · 42 秒）”；失败时保持展开。
- 候选池、对话和两类报告共用同一套计划/工具/进度工作区；任务结束后自动折叠，刷新页面可从持久事件恢复。

## 5. 验收

- 候选池与报告均在首个 1 秒内展示第一条进度事件。
- 实际工具调用能产生至少一条对应安全摘要。
- 结果后面板自动折叠，展开后可回看完整安全轨迹。
- 前端不能获得 Python 内部地址、密钥、Prompt 或原始思维链。
- 长连接中断时，前端不得直接展示浏览器原始文案（如 `Load failed`）；需转为可理解的网络恢复提示。生产反向代理必须对 Agent SSE 路由禁用压缩与响应缓冲。
- 响应必须为 `text/event-stream`，设置 `Cache-Control: no-cache, no-transform` 与 `X-Accel-Buffering: no`；进入 `COMPLETED / WAITING_FOR_USER / FAILED / CANCELLED / EXPIRED` 后发送 `run_status` 再关闭连接。
