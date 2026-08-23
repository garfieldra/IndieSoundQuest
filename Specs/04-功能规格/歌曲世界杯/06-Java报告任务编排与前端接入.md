# Java 报告任务编排与前端接入

**状态：** 已确认 v1.0  
**最后更新：** 2026-08-05  
**前置规格：** `05-赛后偏好报告与长图.md`、`03-技术架构/09-Python报告Agent技术规格.md`、`03-技术架构/10-Agent运行时与多Agent协作深化规格.md`

## 1. 目标

把已完成赛事接入赛后报告 Agent，形成完整闭环：

```text
赛事完成 → Java 创建报告任务 → Python Agent 分析 → Java 持久化 → 前端轮询 → 展示报告
```

本篇只负责任务编排、状态、接口和最小展示，不实现报告长图导出。

## 2. Java 职责

- 校验赛事属于当前访客且状态为 `COMPLETED`；
- 创建报告版本和任务状态；
- 调用 Python 内部 SSE 接口；
- 解析并校验最终 JSON；
- 复核歌曲/艺人 ID 是否存在于规范目录；
- 写入 `tournament_preference_report`；
- 对前端隐藏 Python 地址、Token 和内部事件；
- 处理幂等、超时、失败和手动重试。

Python 不直接写 MySQL，也不决定报告版本号。

## 3. 公共 API

### 3.1 创建报告

```http
POST /api/v1/tournaments/{tournamentId}/preference-report
X-Guest-Session-Id: <guest-session>
```

请求体：

```json
{"force": false}
```

响应：

```json
{
  "reportId": "uuid",
  "tournamentId": "uuid",
  "version": 1,
  "status": "PENDING"
}
```

已有 `READY` 且 `force=false` 时直接返回最新版本；`force=true` 创建递增版本。

### 3.2 查询报告

```http
GET /api/v1/tournaments/{tournamentId}/preference-report
```

返回状态：

- `PENDING`：任务已创建；
- `RUNNING`：Agent 正在执行；
- `READY`：返回完整报告和展示元数据；
- `FAILED`：返回稳定错误码和可重试提示。

前端轮询间隔 1.5 秒，最长 90 秒；超时后保留任务继续运行，并允许用户稍后再次打开页面。

## 4. Java-Python 内部协议

Java 调用：

```http
POST /internal/v1/workflows/tournament-report:stream
Authorization: Bearer <AGENT_INTERNAL_SERVICE_TOKEN>
X-Request-Id: <uuid>
Content-Type: application/json
```

请求字段：

```json
{
  "requestId": "uuid",
  "reportId": "uuid",
  "tournamentId": "uuid",
  "guestId": "guest-session-id",
  "tournamentVersion": 1,
  "includePersonalityEasterEgg": true
}
```

Java 只接受一个终态事件：`result` 或 `error`。中间 `stage_started` 仅写日志，不直接持久化。

## 5. 状态机与重试

```text
PENDING → RUNNING → READY
                   ↘ FAILED
FAILED → PENDING（用户手动重试）
```

- Agent 暂时性错误最多由 Python 自动重试两次；
- Java 调用超时或连接错误不立即覆盖已有 `READY` 版本；
- 同一报告版本只能有一个运行任务；
- 失败记录保留错误码，不保存半成品 JSON；
- `READY` 版本不可覆盖，只能创建新版本。

## 6. 前端交互

- 赛事完成页显示“生成本场偏好报告”；
- 点击后立即展示分析进度，不阻塞赛事结果页面；
- `READY` 后显示摘要、偏好维度、歌曲推荐、艺人推荐和人格彩蛋；
- `FAILED` 显示可重试按钮和简洁错误提示；
- 卡片链接由 Java 生成，前端不拼接平台链接；
- 本篇不加入长图导出按钮，后续单独实现。

## 7. 验收标准

- [ ] 未完成赛事不能创建报告；
- [ ] 非当前访客不能读取或创建报告；
- [ ] 创建接口返回 `202` 或幂等的已有报告状态；
- [ ] Java 能消费 Python SSE 终态并保存报告；
- [ ] 前端能轮询并展示 `PENDING/RUNNING/READY/FAILED`；
- [ ] Agent 失败不会破坏赛事结果和投票数据；
- [ ] 同一版本不会重复调用模型；
- [ ] 推荐实体经过 Java 复核后才返回前端。
