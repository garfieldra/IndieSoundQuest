# Agent 滚动计划与可视化执行面板

**状态：** 已实现、已验证  
**适用范围：** 候选歌曲池 Agent、赛后报告 Agent

## 目标

让用户看到 Agent 自主维护的公开执行计划，而不是原始思维链。计划由 ReAct Supervisor 的真实动作、候选数量和已核验事实驱动更新；每次更新是完整快照，前端可安全替换展示。

本设计借鉴 LangChain `TodoListMiddleware` 的结构化 `write_todos` 模式：一次写入完整任务列表，避免并发局部更新导致状态冲突；但继续使用本项目已有的 LangGraph 状态与 Java SSE 边界。

## 计划事件

```text
event: plan_updated
data: {
  "runId":"uuid",
  "revision":3,
  "goal":"为 32 首赛事准备 64 首可核验候选",
  "summary":"已核验 48 / 64 首，正在补充相近音乐方向",
  "changeSummary":"根据最近一次网络发现结果调整计划",
  "items":[
    {"id":"artist-catalog","title":"核验明确艺人的作品","status":"completed","detail":"已获得 42 首可用歌曲"},
    {"id":"adjacent-discovery","title":"探索相近音乐方向","status":"running","detail":"正在整理公开资料"}
  ]
}
```

状态仅限 `pending`、`running`、`completed`、`skipped`、`blocked`。每次最多展示五项；不得包含 Prompt、原始 CoT、模型评分、密钥、内部 URL 或未过滤网页全文。

## Agent 边界

- ReAct Supervisor 仍自主决定工具、子任务、顺序和停止条件；计划不是固定工作流。
- 运行时根据 `action_history` 中已经实际选择的 ReAct 动作与已验证事实投影公开计划；不预先生成固定阶段模板。工具失败、动作变化、数量变化或目标达成会触发完整的新 revision。
- 同类动作允许重复出现，并以 occurrence 生成稳定条目 ID；界面最多保留最近五步，使计划真实反映搜索、核验、降级与重新规划。
- 候选池与报告使用同一事件协议，但各自定义符合业务语义的计划项。
- Java 只转发安全的 `plan_updated`；最终候选/报告仍由 Java 校验和持久化。

## 前端

- 运行开始后展示“探索计划”卡片，显示目标、完成数、当前项和 `changeSummary` 最近调整说明。
- 每次 `plan_updated` 用完整快照替换 UI；收到最终结果后自动折叠，可手动展开。
- 计划卡与执行时间线并列：前者回答“准备做什么”，后者回答“刚完成了什么”。

## 验收

- 两类 Agent 在首次 ReAct 决策后发送首份计划。
- 候选数量、当前动作或失败状态变化时 revision 递增。
- 前端不需要轮询即可显示计划变更。
- 计划与进度事件都不能泄漏原始模型推理。

## 本次验证记录

- 前端生产构建通过，Vitest 21 项测试通过。
- 候选池真实 AgentRun SSE 调用收到 16 次动态计划更新，revision 从 1 严格递增到 16；修复 MusicBrainz 瞬时空结果后，同一三艺人输入生成 32 条规范候选（16 主池 + 16 补位）。
- 赛后报告真实 AgentRun SSE 调用收到 10 次计划更新以及结构化工具完成/降级事件，最终持久化 5 首歌曲和 2 位艺人推荐。
- 普通对话与非赛事探索报告也通过同一事件流完成；探索报告卡片已持久化至原会话。
