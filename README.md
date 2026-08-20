![RouteBook Agent 首页](docs/assets/system-running-00-home.png)

# RouteBook Agent

RouteBook Agent（路书 Agent）是一套基于对话的智能旅行规划系统。用户只需描述目的地、时间、出行方式和偏好，系统即可完成需求理解、真实地点检索、候选推荐、分日编排、路线与天气查询，并生成可继续编辑和分享的可视化路书。

项目采用 Next.js、FastAPI、LangGraph、Celery、PostgreSQL 和 Redis 构建，地图与地点数据接入高德开放平台，天气数据接入和风天气，需求理解使用 Anthropic API 或兼容 Anthropic Messages API 的模型服务。

## 系统功能与能力

- **对话式需求理解**：从自然语言中提取目的地、日期、天数、出发地、交通方式、必去地点、节奏和偏好；信息不足时最多提出三个关键问题。
- **真实地点检索与推荐**：通过高德 POI 搜索召回真实地点，完成类别规范化、硬过滤、评分、去重和多样性选择，并展示推荐理由与交通取舍。
- **地点消歧与反馈学习**：对同名景区、入口、停车场、车站、游客中心和商户进行区分；支持接受、拒绝和替换候选，并依据拒绝原因调整后续推荐。
- **智能行程编排**：根据轻松、适中、紧凑三种节奏，将地点按区域、优先级和通行成本安排到每日行程，并进行容量、跨区和必去约束检查。
- **路线、地图与天气**：查询驾车/步行路线、距离和耗时，展示地图标记与完整路线，并聚合三日天气、24 小时天气和灾害预警；外部服务异常时支持缓存和降级。
- **持续编辑与版本管理**：支持增加、删除、替换地点，修改指定日期或总天数；重要修改先生成预览提案，确认后再提交。每次修改产生不可变版本，并支持撤销和历史查看。
- **异步工作流与可靠恢复**：使用 LangGraph checkpoint、Celery 和 SSE 实现异步执行、进度推送、中断恢复、幂等重试和版本冲突保护。
- **固定版本分享**：生成不可猜测的分享链接并绑定指定版本；后续修改草稿不会改变已经分享的内容，默认隐藏地点精确地址。

## 运行界面


### 选择推荐地点

系统结合已确认的出行需求，从真实 POI 中召回候选地点，并综合天气状况、地点距离、用户偏好、热门评分、区域分布与交通成本等因素进行分析和排序，给出清晰的推荐理由与评分依据，帮助用户快速完成地点选择。

![地点推荐与选择](docs/assets/system-running-02-place-selection.png)

### 查看完整路线

系统将生成的路书以地图形式直观呈现，集中展示每日地点分布、游览顺序、路线连接、交通距离与行程信息；地图标记与分日计划相互联动，让用户能够快速理解整段旅程的空间布局和每日路线安排。

![完整路书地图](docs/assets/system-running-04-full-route-map.png)

## 快速安装（推荐 Docker）

### 环境要求

- Docker 与 Docker Compose
- 可用的模型 API Key
- 高德开放平台 Web 服务 API Key
- 和风天气 API Key 与专属 API Host
- 可选：高德 JavaScript API Key（未配置时使用内置坐标地图）

### 1. 获取代码并准备配置

```bash
git clone <repository-url>
cd RouteBook-Agent
cp .env.example .env
```

编辑 `.env`，填写模型、高德 Web 服务与和风天气配置：

```dotenv
ANTHROPIC_BASE_URL=https://api.anthropic.com
MODEL_ID=claude-haiku-4-5
ANTHROPIC_API_KEY=your-model-api-key

AMAP_API_KEY=your-amap-web-service-key

QWEATHER_API_KEY=your-qweather-api-key
QWEATHER_API_HOST=your-api-host
```

### 2. 启动完整系统

```bash
docker compose up --build
```

首次启动会自动创建 PostgreSQL、Redis，执行数据库迁移和 LangGraph checkpoint 初始化，然后启动 API、Worker 和 Web。服务就绪后访问：

- Web 工作台：<http://localhost:3000>
- FastAPI 接口文档：<http://localhost:8000/docs>
- 服务就绪检查：<http://localhost:8000/health/ready>

后台启动和停止：

```bash
docker compose up -d --build
docker compose down
```

数据库数据保存在 Docker volume 中，普通 `docker compose down` 不会删除已有路书数据。

## API 与第三方服务配置

所有配置均从项目根目录的 `.env` 或运行环境变量读取。不要提交真实 Key；`.env` 已被 Git 忽略。

### 模型 API

系统通过 Anthropic Python SDK 调用模型，并使用严格 Structured Output 提取旅行需求。

| 变量 | 是否必需 | 说明 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | 是 | 模型服务的 API Key |
| `ANTHROPIC_BASE_URL` | 是 | Anthropic 官方地址或兼容 Anthropic Messages API 的服务地址 |
| `MODEL_ID` | 是 | 服务端可用且支持当前结构化输出调用的模型 ID |
| `REQUIREMENT_TIMEOUT_SECONDS` | 否 | 单次需求提取超时，默认 `20` 秒 |
| `REQUIREMENT_MAX_ATTEMPTS` | 否 | 结构化输出失败后的最大尝试次数，默认 `2` |

Anthropic 官方服务示例：

```dotenv
ANTHROPIC_BASE_URL=https://api.anthropic.com
MODEL_ID=claude-haiku-4-5
ANTHROPIC_API_KEY=your-anthropic-api-key
```

兼容服务示例（地址、模型名以服务商文档为准）：

```dotenv
ANTHROPIC_BASE_URL=https://your-provider.example.com/api/anthropic
MODEL_ID=your-model-id
ANTHROPIC_API_KEY=your-provider-api-key
```

> 兼容端点需要支持 Anthropic Messages API；模型还应支持原生 Structured Output，或能够按要求稳定调用工具并返回严格 JSON Schema。仅兼容普通文本对话的端点无法完成需求提取。

### 高德地图与 POI

| 变量 | 是否必需 | 说明 |
| --- | --- | --- |
| `AMAP_API_KEY` | 是 | 后端使用的“Web 服务 API”Key，用于 POI、地理编码和路线查询 |
| `AMAP_BASE_URL` | 否 | 默认 `https://restapi.amap.com` |
| `NEXT_PUBLIC_AMAP_JS_KEY` | 否 | 浏览器使用的 JavaScript API Key，用于加载高德交互地图 |
| `NEXT_PUBLIC_AMAP_SECURITY_CODE` | 否 | 高德控制台启用安全密钥后填写 |

后端 Web 服务 Key 与浏览器 JavaScript API Key 类型不同，请分别创建。未配置 JavaScript API Key 时，工作台会自动使用内置坐标地图，不影响核心规划流程。

### 和风天气

```dotenv
QWEATHER_API_KEY=your-qweather-api-key
QWEATHER_API_HOST=your-api-host
```

`QWEATHER_API_HOST` 应填写和风天气控制台分配的 API Host，可包含或省略 `https://`。生成完整分日行程和执行行程编辑时会初始化天气适配器，因此正常使用完整规划流程需要同时配置这两个变量；供应商请求暂时失败时，工作流会按可用数据进行部分降级。

### 数据库与 Redis

Docker Compose 已提供开发环境默认值。只有在复用外部服务或本地分别启动各组件时才需要调整：

```dotenv
DATABASE_URL=postgresql+psycopg://routebook:routebook@localhost:5432/routebook
LANGGRAPH_DATABASE_URL=postgresql://routebook:routebook@localhost:5432/routebook?options=-csearch_path%3Dlanggraph
REDIS_URL=redis://localhost:6379/1
CELERY_BROKER_URL=redis://localhost:6379/0
```

## 本地开发安装

除 PostgreSQL 16+ 和 Redis 外，需要安装：

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 24
- pnpm 10（可通过 Corepack 启用）

### 1. 安装后端依赖并初始化数据库

```bash
cp .env.example .env
uv sync --frozen --extra dev --python 3.12
uv run alembic upgrade head
uv run python -m services.api.app.checkpoint_setup
```

若不希望在本机安装 PostgreSQL 或 Redis，可只启动基础设施：

```bash
docker compose up -d postgres redis
```

### 2. 分别启动后端服务

在两个终端中运行：

```bash
uv run uvicorn services.api.app.main:app --reload
```

```bash
uv run celery -A services.api.app.worker:celery_app worker --loglevel=INFO
```

### 3. 安装并启动 Web

```bash
cd apps/web
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Web 默认通过 `http://localhost:8000` 访问 API。如需修改，本地开发可在 `apps/web/.env.local` 中设置 `API_INTERNAL_URL`；后端跨域来源通过根目录 `.env` 的 `API_CORS_ORIGINS` 设置。Docker Compose 则直接读取根目录 `.env`。

## 测试与质量检查

```bash
# 后端测试与静态检查
uv run pytest
uv run ruff check .
uv run mypy services scripts

# Web 测试与构建检查
cd apps/web
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

普通测试使用 `tests/fixtures/providers/` 下的脱敏响应，不会访问真实供应商。显式执行真实高德/和风烟测：

```bash
RUN_PROVIDER_LIVE_TESTS=1 uv run pytest -m provider_live
```

显式调用已配置模型进行需求提取校准：

```bash
uv run python -m scripts.evaluate_phase3_requirements --live
```

## 技术架构

| 层级 | 主要技术 | 职责 |
| --- | --- | --- |
| Web | Next.js 16、React 19、TypeScript | 对话、候选选择、行程、地图、版本与分享界面 |
| API | FastAPI、Pydantic、SQLAlchemy | 业务 API、Schema 校验、版本和幂等控制 |
| Workflow | LangGraph、PostgreSQL Checkpointer | 需求、推荐、编排、编辑流程及中断恢复 |
| Worker | Celery、Redis | 异步工作流执行、任务重投和进度事件 |
| Storage | PostgreSQL、Redis | 业务数据与不可变版本；缓存、消息代理和实时进度 |
| Providers | Anthropic-compatible、Amap、QWeather | 模型理解、真实 POI/路线和天气事实 |

## 项目文档

- [文档中心](docs/README.md)：需求、设计、开发记录和调研索引
- [一期功能需求](docs/requirements/01-路书Agent一期功能需求.md)：一期范围与验收标准
- [一期技术方案](docs/design/02-一期技术方案.md)：系统架构与关键设计
- [发布与运维手册](docs/operations/phase8-runbook.md)：部署、回滚、备份、故障恢复和监控
- [阶段八退出审计](docs/evaluation/phase8-exit-audit.md)：发布门禁与待验证证据

## 安全提示

- 不要将 `.env`、API Key、数据库密码或分享 token 提交到仓库。
- 生产环境设置 `APP_ENV=production`；系统会拒绝 HTTP CORS、默认数据库凭证和 `DEBUG` 日志。
- 浏览器高德 Key 与服务端 Key 必须分离，并通过各自平台的安全策略限制来源与配额。
- 生产发布前请执行 `uv run python -m scripts.release_gate`，并按照运维手册完成备份恢复与故障演练。
