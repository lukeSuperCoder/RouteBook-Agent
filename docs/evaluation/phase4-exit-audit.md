# 第四阶段退出审计：推荐与地点确认

> 审计日期：2026-08-13  
> 对照基线：`docs/design/03-一期开发计划.md` 第 8 节  
> 结论：阶段四领域核心、持久化、API、确定性测试与 PostgreSQL 集成验证通过。

## 1. 主要任务证据

| 任务 | 实现证据 | 验证证据 | 状态 |
| --- | --- | --- | --- |
| RecommendationStrategy | `recommendations/models.py`、`strategy.py` | 主题、地理范围、负面类型、远郊反馈测试 | 通过 |
| 多查询召回与选择 | `recommendations/service.py` | 硬过滤、偏好评分、去重、多样性和拒绝排除测试 | 通过 |
| proposed 候选持久化 | `models.py`、迁移 `20260813_0003` | PostgreSQL 迁移和 API 集成测试 | 通过 |
| PlaceResolutionSubgraph | `recommendations/resolution.py` | 自动采用、泛指概念、interrupt/resume 测试 | 通过 |
| 接受、拒绝和替换 | feedback API、`persistence.py` | 状态转换、拒绝原因、即时替换重排 | 通过 |
| 候选 DTO | `schemas.py`、OpenAPI | 名称、类型、地址、行政区、理由、交通取舍和证据 | 通过 |
| 可观测指标 | recommendations metrics API | 接受率、更正率和拒绝原因分布集成测试 | 通过 |

## 2. 关键业务门禁

- 推荐只消费阶段三的已确认需求，不回写需求提取 Prompt；
- 交通、入口、服务设施、商户及负面类型在进入排序前硬过滤；
- 去重使用稳定的供应商和 POI ID，不使用名称碰撞作为事实身份；
- 推荐候选初始状态固定为 `proposed`，接受前不会进入后续规划输入；
- “长城”“古镇”等泛指概念固定进入偏好选择，不允许高分静默覆盖；
- 候选消歧只允许选择质量门禁后的集合，恢复载荷不能注入任意 POI；
- 拒绝和替换必须提供原因，被拒绝 POI 在当前路书后续召回中排除；
- `too_far` 会覆盖原远郊接受偏好，后续策略固定收紧为不接受远郊；
- 推荐批次绑定不可变基础版本，需求版本变化时拒绝保存陈旧推荐。

## 3. 验证结果

- Ruff 全仓通过；
- 严格 Mypy 通过（39 个源文件）；
- 默认 pytest：48 项通过、2 项真实供应商测试跳过；
- 阶段四领域测试：6 项通过；
- PostgreSQL/Redis 集成：7 项通过，包含推荐保存、反馈和指标 API；
- Alembic `20260813_0002 -> 20260813_0003` 在 PostgreSQL 18 容器执行通过；
- OpenAPI 和 RouteBook Snapshot JSON Schema 已重新导出；
- 默认测试仅使用确定性候选，不访问真实供应商或暴露密钥。

## 4. 阶段五边界

阶段五应只读取状态为 `accepted` 的推荐候选，或由完整自动采用策略记录的真实 POI；
它负责每日容量、顺序、正式路径查询和可行性修复，不得绕过本阶段的地点质量门禁。
