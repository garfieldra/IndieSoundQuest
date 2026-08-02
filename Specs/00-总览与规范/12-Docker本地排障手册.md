# Docker 本地排障手册

**状态：** 已验证  
**最后更新：** 2026-08-02  
**适用范围：** IndieSoundQuest 的 macOS + Docker Desktop 本地开发环境

## 1. 目的

这是一份基于首次 Java 服务启动实测沉淀的手册。出现“Docker 已打开但 `localhost:8080` / Swagger 无法访问”时，按下面顺序检查；不要先删除 Volume 或反复重装 Docker。

## 2. 30 秒快速判断

在项目根目录执行：

```bash
docker compose --env-file .env.example -f infra/compose.yaml ps --all
docker compose --env-file .env.example -f infra/compose.yaml logs --tail=120 java-service
```

判断规则：

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| `java-service` 显示 `healthy` | 服务已正常运行 | 直接访问 `/swagger-ui/index.html` |
| `Exited` 或不断重启 | Java 应用启动失败 | 以 `logs` 最后一个 `ERROR` 为准处理 |
| 没有任何 IndieSoundQuest 容器 | Compose 未成功启动或当前目录/配置文件不对 | 回到第 3 节启动 |
| 停在 `Building` | 构建依赖或网络问题 | 看第 4 节 |

可访问的验收地址：

```text
http://localhost:8080/actuator/health
http://localhost:8080/swagger-ui/index.html
```

健康检查返回 `{"status":"UP",...}` 且 Swagger 返回 HTTP 200，才算真正启动成功。

启动成功后，可再运行完整业务冒烟验证：

```bash
python3 Demos/verify_tournament_api.py
```

该脚本会创建独立的 16 首赛事，验证赛程、封面、全部投票、冠军结算与幂等重放；它不会修改已有赛事。

## 3. 正确的本地启动顺序

```bash
cd /Users/wangrui/Documents/Projects/IndieSoundQuest
mvn -q -DskipTests package -f java-service/pom.xml
docker compose --env-file .env.example -f infra/compose.yaml up -d --build mysql redis java-service
```

当前 Compose 的 `java-service` 使用宿主机打包出的 JAR 运行。这是针对 Docker 虚拟机中 Maven 下载不稳定的临时且可靠的本地开发方案：先在宿主机打包，再由 Docker 负责运行和依赖编排。

不要把 `docker compose up` 的命令返回当成启动成功；必须执行第 2 节的状态与健康检查。

## 4. Docker 拉镜像或 Maven 下载失败

### 4.1 Docker Hub 拉取失败

常见错误：

```text
failed to fetch anonymous token
connect: connection refused
i/o timeout
```

先确认 Docker Desktop 的 Engine 为运行状态。若本机通过 RabbitPro 等 HTTP 代理联网，在 Docker Desktop 的 **Settings → Resources → Proxies** 中选择手动代理，并填写：

```text
HTTP Proxy:  http://127.0.0.1:7897
HTTPS Proxy: http://127.0.0.1:7897
Bypass:      127.0.0.1,localhost
```

这里必须使用 `127.0.0.1`。本次实测 `host.docker.internal:7897` 在当前 Docker Desktop 环境中无法解析，会报：

```text
lookup host.docker.internal: no such host
```

保存设置后重新执行构建。代理端口应以 VPN 客户端实际显示的端口为准，`7897` 只是本次环境的值。

### 4.2 容器内 Maven Central 下载不完整

典型表现：

```text
Premature end of Content-Length delimited message body
Remote host terminated the handshake
repo.maven.apache.org: Name or service not known
```

这不是 Java 编译错误，而是 Docker 虚拟机经代理访问 Maven 仓库不稳定。处理优先级：

1. 先用宿主机执行第 3 节的 Maven 打包；
2. 再运行 Compose；
3. 若宿主机 Maven 也失败，再检查 VPN 是否启用“代理全部流量”以及代理端口是否仍有效。

`java-service/Dockerfile` 中保留了 Maven 构建阶段、下载重试和缓存配置，供 CI 或网络稳定环境使用；本地 Compose 显式选择 `local-runtime` 阶段，避免每次启动都在容器内下载依赖。

## 5. Java 容器启动后立刻退出

先读取完整日志：

```bash
docker compose --env-file .env.example -f infra/compose.yaml logs --tail=200 java-service
```

本项目首次启动时实际遇到过以下错误：

```text
Schema-validation: wrong column type encountered in column [token_hash]
found [char], but expecting [varchar(255)]
```

根因是 MySQL 迁移中 `guest_session.token_hash` 定义为 `CHAR(64)`，而 `GuestSession` 实体若未显式声明会被 Hibernate 视为 `VARCHAR(255)`。修复已写入实体：

```java
@Column(name = "token_hash", nullable = false, unique = true, columnDefinition = "CHAR(64)")
```

以后新增或调整 Flyway 字段时，必须同时确认 JPA 实体的类型、长度和固定/可变字符语义；`spring.jpa.hibernate.ddl-auto=validate` 会在启动阶段阻止这种不一致，这是预期的保护机制，不应关闭校验来绕过问题。

## 6. 处理原则

- 先看容器状态和日志，再修改配置；不要根据浏览器错误页猜测。
- 先修复构建/启动的第一个明确错误，再重新验证；后续错误可能在前一个错误消失后才出现。
- 不要将 `docker compose down -v` 作为排障的第一选择：它会删除 MySQL、Redis 等本地演示数据。
- Docker Desktop 显示 Engine running 不代表项目服务已经启动；需要看到 `indie-sound-quest-java-service-1` 为 `healthy`。
- Swagger 推荐使用完整入口 `/swagger-ui/index.html`，避免不同 springdoc 版本对目录重定向的差异。

## 7. 本次修复的最终验收记录

2026-08-02，本地环境已满足：

- MySQL：healthy；
- Redis：healthy；
- Java Service：healthy，端口映射 `localhost:8080 → 8080`；
- `GET /actuator/health`：返回 `{"status":"UP","groups":["liveness","readiness"]}`；
- `GET /swagger-ui/index.html`：HTTP 200；
- Flyway：4 个迁移校验并执行成功。
