# 阶段八发布与故障恢复手册

## 1. 发布门禁

发布候选必须固定依赖锁文件、Prompt 版本、模型 ID、POI 阈值和容器镜像摘要。先执行：

```bash
uv run python -m scripts.release_gate
```

CI 还必须在 PostgreSQL、Redis 就绪后执行迁移、LangGraph checkpoint 初始化和带
`RUN_INTEGRATION_TESTS=1` 的全量测试。真实供应商烟测只能手动触发
`provider-live.yml`，凭证来自受保护 Environment，不进入日志或构建产物。

发布批准条件：所有自动门禁通过；P0/P1 缺陷为零；最近一次备份恢复和依赖故障演练
有记录；监控与告警接收人已确认。任何一项缺失都停止发布。

## 2. 部署

1. 记录 Git commit、镜像摘要、数据库 revision、Prompt 版本和阈值配置。
2. 备份 PostgreSQL；验证备份文件非空，并在隔离数据库完成恢复抽查。
3. 先执行 `alembic upgrade head` 和 checkpoint 初始化，再启动 API/Worker，最后启动 Web。
4. 验证 `/health/live` 与 `/health/ready`，创建合成路书，确认 SSE、Worker 和版本提交。
5. 逐步放量，观察至少 30 分钟的错误率、P95、供应商失败率和 LLM 成本。

生产环境 `APP_ENV=production` 会拒绝 HTTP CORS、示例数据库凭证和 DEBUG 日志。
浏览器高德 Key 与服务端 Key 必须分开，所有密钥由部署平台注入。

## 3. 回滚

应用回滚使用上一个已验证镜像摘要，不使用浮动 tag。先停止放量，再回滚 Web、API 和
Worker。数据库迁移默认只前向修复；只有确认当前 revision 的 downgrade 不丢数据且已
完成备份恢复验证时才允许数据库回退。回滚后重新执行 readiness 和合成路径，并记录
故障时间线、受影响 routebook/workflow run 和人工处置。

## 4. 备份与恢复

备份示例（实际凭证由安全环境注入）：

```bash
pg_dump --format=custom --no-owner --file=routebook.dump "$DATABASE_URL"
pg_restore --clean --if-exists --no-owner --dbname="$RESTORE_DATABASE_URL" routebook.dump
```

至少每日备份，保留 30 天；每月在隔离环境恢复一次。恢复后执行迁移、checkpoint 初始化、
行程版本计数核对和分享页抽查。Redis 只承载 broker、进度与缓存，不作为业务真相；恢复
Redis 后允许缓存重建，未确认任务按 operation/message/idempotency key 安全重投。

## 5. 故障演练与人工重试

仅在本地隔离 compose 环境执行破坏性演练：

```bash
RUN_PHASE8_FAULT_DRILL=1 bash scripts/phase8_fault_drill.sh
```

脚本验证 Redis/PostgreSQL 中断时 readiness 失败、依赖恢复后服务恢复，以及 Worker 重启。
外部供应商超时由 fixture 契约测试覆盖；真实演练应使用供应商测试配额并确认 stale 降级。

人工重试前先查询 workflow run、当前 version 和 operation/message ID：

- 已成功提交版本：不得重新创建，返回已有结果；
- Worker 失败且未提交：使用原任务 ID 重投；
- `VERSION_CONFLICT`：重新读取当前版本，不强制覆盖；
- 供应商失败：在限额内重试，否则保留 unverified/unavailable 状态；
- 分享泄露：立即调用分享撤销接口，确认旧 token 返回 404，再生成新 token。

## 6. 监控与告警

平台从结构化日志聚合以下指标，并按环境冻结阈值：

| 指标 | 告警建议 |
| --- | --- |
| API 5xx / readiness | 5 分钟错误率 > 1%；readiness 连续失败 2 分钟 |
| 核心 API 与 Graph P95 | 连续 10 分钟超过发布基线 1.5 倍 |
| Worker 失败/重试/队列深度 | 失败率 > 2%；最老任务等待 > 5 分钟 |
| LLM token、费用、超时 | 日预算 80% 预警，100% 限流；超时率 > 5% |
| 高德/和风失败与缓存 stale | 失败率 > 5%；stale 使用率异常上升 |
| POI 更正、错误采用、修复不收敛 | 错误自动采用立即 P1；趋势超过冻结基线告警 |

日志禁止精确住宅地址、Authorization、API Key、分享 token 和密码。告警通知必须包含
request/routebook/workflow/version ID，不包含用户原始敏感文本。
