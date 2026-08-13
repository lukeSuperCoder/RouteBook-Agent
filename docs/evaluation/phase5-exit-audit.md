# 第五阶段退出审计：行程与路线规划

> 审计日期：2026-08-13  
> 对照基线：`docs/design/03-一期开发计划.md` 第 9 节  
> 结论：阶段五启发式规划、正式路线、可行性修复、天气组装、版本提交与集成验证通过。

## 1. 主要任务证据

| 任务 | 实现证据 | 验证证据 | 状态 |
| --- | --- | --- | --- |
| DailyCapacity 模板 | `planning/models.py` | 三档容量顺序测试 | 通过 |
| 分组与必去优先 | `planning/service.py` | 1～7 天矩阵、必去保留测试 | 通过 |
| 最近邻与有限 2-opt | `planning/optimizer.py` | 优化后近似长度不增加 | 通过 |
| 正式相邻路线 | `planning/service.py` | 距离与耗时仅取自 RouteResult | 通过 |
| 可行性检查 | `_preflight`、`_validate` | 必去、排除、容量、跨区、远郊、类型检查 | 通过 |
| 三轮有界修复 | `_repair` | 低优先级移除与必去不可删除测试 | 通过 |
| 天气与预警 | `_fetch_weather` | 并行查询、不可用降级测试 | 通过 |
| API 与不可变版本 | itinerary API、`planning/persistence.py` | PostgreSQL 集成测试 | 通过 |

## 2. 关键业务门禁

- 只有阶段四中 `accepted` 或留有 `auto_adopted` 证据的真实 POI 能进入规划；
- 未解析的必去文本直接返回 `unresolved_must_visit`，不会按名称猜测；
- 规划要求 1～7 天、3～15 个已确认地点，超出范围返回结构化冲突；
- GCJ-02 近似距离只用于初排和 2-opt，不写入用户可见路线事实；
- 用户可见距离和耗时只复制路径适配器返回的 `RouteResult`；
- 路线查询失败保留相邻地点并创建 `unverified` 路线段；
- 天气失败保留行程并创建 `unavailable` 天气事实；
- 自动修复只移除 `accepted` 低优先级地点，绝不移除 `must_visit`；
- 修复循环最多三轮，仍不可行时返回 `repair_exhausted`；
- 版本提交使用基础版本校验，不允许陈旧规划覆盖更新后的路书。

## 3. 验证结果

- Ruff 全仓通过；
- 严格 Mypy 通过（45 个源文件）；
- 默认 pytest：64 项通过、2 项真实供应商测试跳过；
- 阶段五领域测试：16 项通过，覆盖 1～7 天矩阵和 LangGraph 子图；
- PostgreSQL/Redis 集成：8 项通过，包含接受候选到行程版本的完整提交；
- OpenAPI 和 RouteBook Snapshot JSON Schema 已重新导出；
- 默认测试只使用确定性路线与天气事实，不调用真实供应商。

## 4. 阶段六边界

阶段六在本阶段生成的不可变行程版本上实现局部编辑、影响范围计算、提案确认和撤销。
未受影响日期必须保持规范化哈希不变，不能用全量重排掩盖局部编辑范围。
