# Agent 运行持久化与对话报告

**状态：** 草案
**依赖：** `03-技术架构/15-Agent控制面、对话报告与偏好反馈规格.md`

## 范围

首期实现可恢复 `AgentRun`、用户澄清卡、事件 SSE 回放、非赛事对话报告入口和推荐反馈写入。统一音乐探索 Agent 可以按需调用候选池、赛事分析、报告撰写等复合工具；世界杯以 `song_world_cup` Skill 形式在对话中提议或启动，完成后向同一会话追加赛事报告卡。

## 用户流程

```text
系统欢迎语 → 用户描述偏好 → Agent 探索/澄清
  ├─ 用户选择“开始歌曲世界杯” → 候选池卡 → 赛事 → 赛事报告卡
  └─ 用户选择“整理我的偏好” → 对话报告卡 → 推荐反馈 → 后续探索
```

## API 摘要

- `GET /api/v1/agent-runs/{id}/events?afterSequence=`：历史事件回放。
- `POST /api/v1/agent-runs/{id}/answers`：提交结构化澄清答案并恢复 Run。
- `POST /api/v1/conversations/{id}/exploration-report`：请求非赛事报告。
- `POST /api/v1/recommendation-feedback`：写入推荐反馈事件。

所有写操作使用访客归属校验、幂等键和 Java Schema 校验。
