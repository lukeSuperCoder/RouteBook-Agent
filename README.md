![RouteBook Agent](docs/assets/routebook-agent-title.png)

# RouteBook Agent

路书 Agent 是一个记录从 0 到 1 完整开发过程的实践项目，旨在构建一款具备流程编排能力的对话式旅行规划工具。它可自主完成需求理解、信息查询、路线规划与结果校验，生成包含每日行程、地图路线和天气信息的可视化路书，并支持持续调整。

## 当前阶段

项目正在进行一期最小功能开发，当前范围包括：

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

仓库包含一个不依赖真实 LLM、地图 API 或数据库的可运行原型，用来演示
State、条件路由、`interrupt()`、checkpoint 和 `Command(resume=...)`。

```bash
uv sync --extra dev --python 3.12
uv run python -m examples.langgraph_minimal
```

直接回车使用内置的南京三日游需求；流程会在“鼓楼”地点消歧处暂停，选择后恢复并生成简化行程。

运行自动化测试：

```bash
uv run pytest
```
