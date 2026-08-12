![RouteBook Agent](docs/assets/routebook-agent-title.png)

# RouteBook Agent

路书 Agent 是一个记录从 0 到 1 完整开发过程的实践项目，旨在构建一款具备流程编排能力的对话式旅行规划工具。它可自主完成需求理解、信息查询、路线规划与结果校验，生成包含每日行程、地图路线和天气信息的可视化路书，并支持持续调整。

## 当前阶段

项目已建立一期第一阶段正式工程基线，当前可运行能力包括：

- FastAPI、Celery、PostgreSQL、Redis 与 LangGraph PostgreSQL Checkpointer；
- 创建路书、异步执行空工作流、保存不可变版本 1；
- 幂等创建、Worker 重投保护、乐观版本冲突与 SSE 进度；
- Next.js 工程状态页和 API/OpenAPI/JSON Schema 合同。

后续一期范围包括：

- 对话式旅行需求收集；
- Agent 流程编排；
- 高德地点检索与路线规划；
- 和风天气预报与灾害预警；
- 按天组织的结构化路书；
- 地图与行程联动；
- 局部修改、确认、版本保存和撤销。

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

直接回车使用内置的南京三日游需求。AI 会先提取目的地、天数和必去地点；如果用户
只提供“北京三日游”这类宽泛需求，AI 会按约一天一个主要地点补充推荐。随后
逐个调用高德 `/v5/place/text` 搜索真实 POI。唯一精确候选会自动确认，多个合理候选
会通过 `interrupt()` 暂停供用户选择，然后恢复并继续搜索剩余地点。

CLI 默认以 `INFO` 级别输出模型调用、LangGraph 节点切换、高德搜索、自动确认、
暂停和恢复日志。日志不会输出 API Key 或包含 Key 的完整请求 URL。

运行自动化测试：

```bash
uv run pytest
```

## 正式工程基线

启动完整第一阶段服务：

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
