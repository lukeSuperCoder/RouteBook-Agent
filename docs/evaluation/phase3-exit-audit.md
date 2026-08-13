# 第三阶段退出审计：需求对话

> 审计日期：2026-08-13  
> 对照基线：`docs/design/03-一期开发计划.md` 第 7 节  
> 结论：阶段三代码、确定性评测、PostgreSQL 中断恢复及真实模型质量校准全部通过。

## 1. 主要任务证据

| 任务 | 实现证据 | 验证证据 | 状态 |
| --- | --- | --- | --- |
| RequirementPatch、来源、置信度、冲突和阻断项 | `services/api/app/requirements/models.py`、`schemas.py` | Schema、领域和对抗用例 | 通过 |
| 严格结构化需求提取 | `requirements/extractor.py`、`prompts.py` | Pydantic Structured Output、兼容端点工具调用兜底、有界重试与失败降级测试 | 通过 |
| 增量合并与确认保护 | `requirements/service.py` | 显式纠正、推断覆盖保护、列表追加/移除测试 | 通过 |
| 安全默认值与最小输入 | `requirements/service.py` | 1～7 天、过去日期、目标地点和三问上限测试 | 通过 |
| RequirementSubgraph | `requirements/graph.py` | MemorySaver 与 PostgresSaver 中断/恢复测试 | 通过 |
| 消息、恢复与幂等 API | `/messages`、`/resume`、`conversation_messages` | PostgreSQL API 集成测试 | 通过 |
| Prompt/模型调用追踪 | `llm_call_records`、`ExtractionTrace` | 两轮调用落库、失败错误码与唯一约束 | 通过 |
| 离线重放评测 | `phase3-requirements.json`、`evaluate_phase3_requirements.py` | 5/5，score 1.000 | 通过 |

## 2. 关键业务门禁

- 模型只输出本轮需求补丁，不推荐景点、不生成路线、天气、坐标或供应商事实；
- 推断值不能覆盖用户明确或已确认值，模型推断地点不能写入用户需求；
- 普通偏好缺失使用来源为 `default` 的透明默认值；
- 每轮最多返回三个阻断问题，结构化输出最终失败时降级为澄清；
- 恢复接口只接受固定 `requirement_clarification` 载荷，不能覆盖任意 Graph State；
- 同一路书的客户端消息 ID 唯一，安全重试不重复写消息或正式版本；
- 需求齐全前只保存消息、追踪与 checkpoint，不修改正式版本；
- 需求齐全后通过原有乐观并发事务提交不可变 RouteBook 版本。

## 3. 验证结果

- Ruff 全仓通过；
- 严格 Mypy 通过（33 个源文件）；
- 默认 pytest：42 项通过、2 项真实供应商测试跳过；
- PostgreSQL/Redis 集成：6 项通过，包含真实 PostgresSaver 暂停、恢复和任务重投；
- 阶段三回放评测：5/5，冻结门槛 1.000；
- 使用已配置 `glm-5` 兼容端点执行 5 条脱敏合成语句，真实模型字段评测 19/19，得分 1.000（门槛 0.900）；
- 兼容端点返回 Markdown 包裹 JSON 时，第二次严格工具调用兜底成功，所有输出继续通过同一 Pydantic Schema；
- OpenAPI 与 RouteBook Snapshot JSON Schema 已重新导出；
- Compose 迁移和 Checkpointer 初始化通过。
- 前端 lint、TypeScript、Vitest 通过；Webpack 生产构建通过。当前执行环境禁止
  Turbopack 内部绑定临时端口，默认 `next build` 因环境策略失败，与本轮后端改动无关。

## 4. 外部验证与后续边界

真实模型调用会把用户消息发送至配置的 `ANTHROPIC_BASE_URL`，因此不纳入普通 CI，只能在
受控环境显式授权后执行。2026-08-13 已完成一次脱敏真实校准；普通 CI 继续使用确定性回放，
避免凭证外发和模型波动阻断代码回归。模型或 Prompt 版本变化后必须重新运行 `--live` 校准。

阶段四将在本阶段输出的已确认需求上实现推荐策略、真实 POI 召回、地点消歧和反馈重排，
不会把推荐职责回填到 `requirement_extraction` Prompt。
