# Spotify 目录发现适配器

- 状态：实施中
- 优先级：P1
- 依赖：`16-在线优先候选发现与意图澄清.md`、`23-候选歌曲证据模型与质量控制.md`、`26-国内内容研究工具接入.md`

## 1. 目标与定位

为候选歌曲池 Agent 增加 Spotify 官方 Web API 的只读目录发现工具，增强国际艺人、英文流派和跨语种偏好的可发现性。

Spotify 不是赛事事实源，也不是最终实体 ID 的权威：它提供候选线索；MusicBrainz 继续负责对齐、去重和规范化入库；Java 目录才是可参赛歌曲的唯一来源。豆瓣维持文化语境/专辑资料的角色，不作为稳定单曲候选目录。

## 2. Agent 自主决策

Candidate ReAct Supervisor 新增可选 `search_spotify` 动作。它根据语言、艺人地域、流派、当前候选数量、已有网络证据与运行预算自主决定是否调用，不能形成“每次先 Spotify 再 MusicBrainz”的固定流程。

```text
Spotify 官方搜索 → Track Hint（歌名、艺人、专辑、封面、Spotify URL、可用 ISRC）
                 → MusicBrainz 核验与导入
                 → Java 规范目录 → 赛事候选池
```

- `ARTIST_LOCKED` 仍只能导入被明确指定艺人的作品；
- Spotify 搜索失败、未配置或限流时，记录受控失败观察并继续现有 Web Search / 国内研究 / MusicBrainz 链路；
- 不调用 Spotify 播放、用户资料、歌单写入、用户库或用户 OAuth。

## 3. 鉴权与数据边界

- 使用 Spotify App 的 **Client Credentials**，仅放在 `agent-service` 环境变量：`SPOTIFY_CLIENT_ID`、`SPOTIFY_CLIENT_SECRET`；
- 访问令牌仅在 Agent 进程内按过期时间缓存，绝不返回 Java、前端、SSE、日志或模型上下文；
- `SPOTIFY_DISCOVERY_ENABLED` 默认 `false`；未配置完整凭证时自动禁用；
- 首版只请求 `/v1/search` 的 `track,artist,album`，每类最多 10 条；只保留受限元数据与 Spotify 公开 URL；
- 不下载、代理或缓存音频/歌词，试听仍沿用现有平台跳转策略。

## 4. 配置与验收

```dotenv
SPOTIFY_DISCOVERY_ENABLED=true
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
# 可选。留空则不强行按国家市场过滤目录结果。
SPOTIFY_MARKET=
```

验收：

1. 无配置时工具为零网络调用的安全降级；
2. 配置后可通过 Client Credentials 获取缓存令牌，并将搜索结果转换为可追溯歌曲线索；
3. Agent 只有在自主选择 `search_spotify` 时才调用；返回线索必须先走 MusicBrainz，不能直接开赛；
4. 搜索来源在候选证据与报告链接中可呈现为 Spotify 目录资料；
5. 单元测试覆盖鉴权缓存、URL 映射、禁用降级和错误降级。
