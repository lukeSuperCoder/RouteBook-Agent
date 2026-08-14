![RouteBook Agent](docs/assets/routebook-agent-title.png)

# RouteBook Agent

路书 Agent 是一个记录从 0 到 1 完整开发过程的实践项目，旨在构建一款具备流程编排能力的对话式旅行规划工具。它可自主完成需求理解、信息查询、路线规划与结果校验，生成包含每日行程、地图路线和天气信息的可视化路书，并支持持续调整。

## 当前阶段

项目已进入一期第八阶段“稳定性与发布”。自动化发布门禁、安全配置和运维基线已建立，生产压测、真实故障演练与小范围试用仍需在目标环境留证。当前可运行能力包括：

- FastAPI、Celery、PostgreSQL、Redis 与 LangGraph PostgreSQL Checkpointer；
- 创建路书、异步执行空工作流、保存不可变版本 1；
- 幂等创建、Worker 重投保护、乐观版本冲突与 SSE 进度；
- Next.js 工程状态页和 API/OpenAPI/JSON Schema 合同；
- 高德 POI、地理编码、V5 驾车和步行路线适配器；
- 和风天气三日、24 小时和灾害预警适配器；
- 供应商超时、有限重试、错误映射、默认启用的 Redis 规范化缓存和 stale 降级；
- POI 类别规范化、主体/入口/交通/服务/商户分类、硬过滤和可配置评分；
- 强制执行自动采用质量门禁的统一地点事实服务；
- 重名景区、直通车、车站、停车场、入口、游客中心和同名商户对抗评测；
- 严格结构化 `RequirementPatch` 提取、字段来源/置信度与显式确认保护；
- 每轮最多三个阻断问题、透明安全默认值与提取失败澄清降级；
- LangGraph PostgreSQL Checkpointer 中断恢复、客户端消息幂等和对话记录；
- Prompt/模型/响应/token/耗时追踪，以及需求快照的不可变版本提交；
- 原生 Structured Output 优先、严格工具调用兜底的 Anthropic 兼容端点策略；
- 阶段三需求回放评测、真实模型校准命令和 CI 门禁。
- 从已确认需求生成推荐策略，执行多查询真实 POI 召回；
- POI 硬过滤、偏好评分、供应商 ID 去重和类别/行政区多样性选择；
- 带理由、交通取舍、来源查询和评分证据的持久化地点候选；
- 高置信自动采用、候选消歧、泛指偏好选择和无结果澄清子图；
- 推荐接受、拒绝、即时替换和基于拒绝历史的后续重排；
- 推荐接受率、用户更正率和拒绝原因分布 API。
- 轻松、适中、紧凑三档每日地点、活动与交通容量模板；
- 按行政区分组、必去优先、最近邻和有限 2-opt 的可解释分日编排；
- 对最终相邻地点调用高德路径适配器，保存正式距离、耗时和验证状态；
- 必去、排除、容量、跨区、远郊混排和类型重复的结构化可行性检查；
- 最多三轮的有界修复，只移除低优先级推荐项，不静默删除必去地点；
- 并行路线、每日天气和灾害预警查询，以及明确的部分失败降级状态；
- 将分日行程、路线段、天气和预警提交为新的不可变路书版本。
- 增加、删除、替换地点、修改指定日期和改变总天数的严格编辑意图；
- “第二天”等日期引用和名称/ID 地点引用解析，歧义时返回候选澄清；
- 受影响日期、地点、路线段、天气位置与主要路线风险计算；
- 只重算受影响日期的正式路线和天气，并校验其他日期规范化哈希；
- 删除必去、修改多日、改变总天数或主要路线时生成隔离的待确认提案；
- 提案接受、拒绝、暂停期间基础版本冲突和编辑 operation ID 幂等保护；
- 撤销复制父版本快照并创建新的 `undo` 版本，不删除历史版本。
- 对话、需求摘要、分日行程和高德地图三栏工作台，未配置或加载失败时自动降级为坐标地图；
- 行程地点与地图标记联动、事实状态图例和提案独立预览；
- SSE 进度与重连提示、澄清恢复、提案确认、撤销和版本历史；
- 最终完整性检查、不可猜测分享令牌和绑定指定版本的固定分享页；
- 默认隐藏地点精确地址，后续草稿修改不覆盖已生成分享页面。

第八阶段发布证据与待签署项见[退出审计](docs/evaluation/phase8-exit-audit.md)，操作步骤见[运维手册](docs/operations/phase8-runbook.md)。

## 项目文档

需求、接口设计和调研记录见 [文档中心](docs/README.md)。

## LangGraph 最小原型

仓库包含一个接入 Anthropic 兼容模型和高德 Web 服务 API 的可运行原型，用来演示
AI 结构化需求提取、真实 POI 搜索、State、条件路由、`interrupt()`、checkpoint
和 `Command(resume=...)`。

```bash
uv sync --extra dev --python 3.12
cp .env.example .env
# 编辑 .env，填写 ANTHROPIC_API_KEY 和 AMAP_API_KEY
uv run python -m examples.langgraph_minimal
```

默认配置使用 `https://open.bigmodel.cn/api/anthropic` 和 `glm-5`。API Key 仅从
`.env` 或当前 shell 环境读取，`.env` 已被 Git 忽略。

`AMAP_API_KEY` 必须是在高德开放平台申请的“Web 服务 API”类型 Key，仅由后端使用。
浏览器地图使用独立的 `NEXT_PUBLIC_AMAP_JS_KEY`；未配置时不会下载第三方地图脚本，
工作台仍可使用内置坐标视图。若高德控制台启用了安全密钥，同时配置
`NEXT_PUBLIC_AMAP_SECURITY_CODE`。

直接回车使用内置的南京三日游需求。AI 会先提取目的地、天数和必去地点；如果用户
只提供“北京三日游”这类宽泛需求，AI 会按约一天一个主要地点补充推荐。随后
逐个调用高德 `/v5/place/text` 搜索真实 POI。唯一精确候选会自动确认，多个合理候选
会通过 `interrupt()` 暂停供用户选择，然后恢复并继续搜索剩余地点。

CLI 默认以 `INFO` 级别输出模型调用、LangGraph 节点切换、高德搜索、自动确认、
暂停和恢复日志。日志不会输出 API Key 或包含 Key 的完整请求 URL。

运行自动化测试：

```bash
uv run pytest
uv run python -m scripts.evaluate_phase3_requirements
```

显式向已配置模型发送脱敏合成语句并执行质量校准：

```bash
uv run python -m scripts.evaluate_phase3_requirements --live
```

真实供应商烟测与普通 CI 隔离，只有显式启用后才会读取服务端凭证并发送请求：

```bash
RUN_PROVIDER_LIVE_TESTS=1 uv run pytest -m provider_live
```

普通 CI 和本地默认测试只使用 `tests/fixtures/providers/` 中的脱敏响应，不访问外网。
仓库的 `Provider Live Smoke Tests` GitHub Actions 工作流只能手动触发，并从受保护的
`provider-live` Environment 读取凭证；它可分别运行高德、和风或全部真实烟测。

## 正式工程基线

启动正式工程服务：

```bash
docker compose up --build
```

该命令提供团队一致的 PostgreSQL 18 + Redis 8 环境。若本机已有 PostgreSQL 16+，可直接复用：只启动缺失的 Redis（`docker compose up -d redis`），并将 `DATABASE_URL`、`LANGGRAPH_DATABASE_URL` 指向专用 RouteBook 数据库，无需下载 PostgreSQL 镜像。

- Web 状态页：`http://localhost:3000`
- FastAPI 文档：`http://localhost:8000/docs`
- 就绪检查：`http://localhost:8000/health/ready`

创建一份路书并调度基础工作流：

```bash
curl -X POST http://localhost:8000/api/routebooks \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: routebook-demo-001' \
  -d '{"title":"武汉三日路书"}'
```

提交第一轮需求消息：

```bash
curl -X POST http://localhost:8000/api/routebooks/<routebook_id>/messages \
  -H 'Content-Type: application/json' \
  -d '{"message_id":"message-demo-001","text":"9月1日从上海自驾去南京玩三天"}'
```

若 Workflow Run 进入 `waiting_for_clarification`，使用同一 Run 恢复：

```bash
curl -X POST http://localhost:8000/api/workflow-runs/<workflow_run_id>/resume \
  -H 'Content-Type: application/json' \
  -d '{"interrupt_kind":"requirement_clarification","message_id":"message-demo-002","text":"从上海出发，自驾"}'
```

每个 `message_id` 在同一路书内必须唯一；相同 ID 和内容可安全重试，不同内容返回
`IDEMPOTENCY_CONFLICT`。模型输出、补丁合并和阻断判断相互分离，模型不会直接写入
路线、天气、坐标或推荐地点事实。

需求确认后生成推荐候选：

```bash
curl -X POST http://localhost:8000/api/routebooks/<routebook_id>/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"limit":8}'
```

候选返回真实 POI 名称、类型、地址、行政区、推荐理由、交通取舍和评分证据。接受、
拒绝或替换候选使用
`POST /api/routebooks/<routebook_id>/recommendations/<proposal_id>/feedback`；拒绝和替换
必须携带标准原因。`too_far` 会关闭本路书后续策略的远郊接受，并且被拒绝的供应商
POI ID 不会再次进入候选。指标可从
`GET /api/routebooks/<routebook_id>/recommendations/metrics` 查询。

至少接受三个真实地点后生成分日行程：

```bash
curl -X POST http://localhost:8000/api/routebooks/<routebook_id>/itinerary
```

成功响应包含新版本 ID、修复轮数和降级标记。仍有未解析必去地点、必去约束与每日
容量不可同时满足，或三轮修复后仍不可行时，响应返回结构化冲突且不提交版本。路线
或天气部分失败时保留有效地点安排，并分别标记未验证路线或不可用天气。

提交局部编辑：

```bash
curl -X POST http://localhost:8000/api/routebooks/<routebook_id>/edits \
  -H 'Content-Type: application/json' \
  -d '{"operation_id":"<uuid>","operation":"edit_day","day_reference":"第二天","note":"下午慢一点"}'
```

重要修改返回 `pending_confirmation` 和独立预览，通过
`POST /api/proposals/<proposal_id>/decision` 接受或拒绝。撤销使用
`POST /api/routebooks/<routebook_id>/undo`，并提供新的 `operation_id`。相同操作 ID
和内容可安全重试，不同内容复用相同 ID 返回 `IDEMPOTENCY_CONFLICT`。

本地分别运行时，先复制 `.env.example` 并启动 PostgreSQL、Redis，然后执行：

```bash
uv sync --extra dev --python 3.12
uv run alembic upgrade head
uv run python -m services.api.app.checkpoint_setup
uv run uvicorn services.api.app.main:app --reload
uv run celery -A services.api.app.worker:celery_app worker --loglevel=INFO

cd apps/web
pnpm install
pnpm dev
```

合同文件由 Pydantic 与 FastAPI 生成；修改 Schema 或 API 后运行：

```bash
uv run python -m scripts.export_contracts
```
