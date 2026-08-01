# IndieSoundQuest

一个以“歌曲世界杯”为入口的个人音乐探索 Agent：用户通过 16/32 首单败淘汰赛选择喜欢的歌曲，系统据此生成可解释的音乐推荐与偏好报告。

## 技术栈

- Web：React + TypeScript + Vite
- 业务服务：Java 21 + Spring Boot 3 + MySQL 8 + Redis
- Agent：Python + FastAPI + LangGraph，默认 DeepSeek
- 知识库：Milvus（仅存带来源的音乐事实索引）
- 本地编排：Docker Compose

## 当前状态

工程骨架已建立。首个实现目标是跑通经典歌曲世界杯：创建 16/32 首赛事、确定性赛程、投票、晋级与冠军。

详细设计见 [Specs](./Specs)。

## 本地启动（骨架）

```bash
cp .env.example .env
docker compose -f infra/compose.yaml up --build
```

启动后可访问：

- Web：<http://localhost:5173>
- Java 健康检查：<http://localhost:8080/actuator/health>

Agent 服务仅在 Docker 内部网络开放；可使用 `docker compose -f infra/compose.yaml exec agent-service curl http://localhost:8000/health/live` 检查其状态。

首次 Docker 构建会下载依赖。未配置 `DEEPSEEK_API_KEY` 时，基础服务仍可启动；Agent 功能在后续实现阶段会明确提示模型不可用。
