# 第二阶段退出审计

> 审计日期：2026-08-13  
> 对照基线：`docs/design/03-一期开发计划.md` 第 6 节  
> 结论：离线开发与契约门禁、和风真实烟测通过；高德凭证轮换与真实烟测待完成

## 1. 主要任务证据

| 任务 | 实现证据 | 验证证据 | 状态 |
| --- | --- | --- | --- |
| 高德 POI、地理编码、驾车、步行 | `services/api/app/providers/amap.py` | `test_amap_normalizes_poi_and_quality_excludes_affiliates`、`test_amap_geocode_and_both_route_modes` | 离线通过 |
| 和风日、小时、预警 | `services/api/app/providers/qweather.py` | 契约测试及 `test_qweather_live_daily_hourly_and_warning` | 真实烟测通过 |
| 超时、限流、错误码、重试 | `providers/http.py`、`errors.py` | `test_http_timeout_is_bounded_and_auth_failure_does_not_retry`、`test_provider_business_rate_limit_is_retried_with_a_bound` | 通过 |
| 缓存与 stale 降级 | `providers/cache.py`；适配器默认装配 `RedisProviderCache` | `test_stale_cache_is_returned_after_timeout`、真实 Redis fresh/stale 往返 | 通过 |
| Place DTO 与 GCJ-02 | `providers/models.py` 的 `PlaceCandidate`、`PlaceFact`；供应商、路线方式、GCJ-02 和时区为强类型合同 | `test_amap_normalizes_poi_and_quality_excludes_affiliates`、`test_fact_models_reject_non_gcj02_coordinates_and_naive_times` | 通过 |
| 类别映射、语义分类、硬过滤和风险词 | `providers/poi_quality.py` | POI fixture 与对抗评测 | 通过 |
| 可配置评分和自动采用 | `config.py`、`PoiScoringConfig`、`PlaceFactService` | 严格阈值、错误行政区、泛指概念和领域入口门禁用例 | 通过 |
| POI 对抗评测集 | `phase2-poi-adversarial.json` | `test_adversarial_poi_evaluation_cases` | 通过 |

## 2. 退出标准证据

| 退出标准 | 证据 | 结论 |
| --- | --- | --- |
| 交通、旅行服务、景区附属设施不自动采用 | 直通车、车站、停车场、入口、游客中心全部命中硬过滤 | 通过 |
| 唯一候选但类别不符进入低置信或无结果 | `unique-transit-is-not-attraction`、`unique-merchant-is-not-attraction` | 通过 |
| 正常、空结果、超时、限流、鉴权、格式异常契约测试 | `tests/test_phase2_providers.py` | 通过 |
| 普通 CI 使用脱敏 fixture | 默认 pytest 不设置 `RUN_PROVIDER_LIVE_TESTS`；fixture 位于 `tests/fixtures/providers/` | 通过 |
| 真实接口测试独立执行且不暴露密钥 | `tests/test_phase2_live_providers.py` 与手动 `Provider Live Smoke Tests` 工作流；鉴权由 `httpx.Auth` 注入 | 和风在线执行通过；高德待凭证轮换 |

## 3. 可靠性与安全回归

- `ruff check .` 通过；
- 严格 `mypy services` 通过；
- OpenAPI/JSON Schema 合同导出检查通过；
- 默认测试 33 项通过、2 项真实供应商测试跳过；
- PostgreSQL 18 空卷启动、Alembic 迁移和 LangGraph Checkpointer 初始化通过；
- Compose 网络中的第一阶段集成回归与第二阶段契约测试共 19 项通过；
- `docker compose config --quiet` 和真实烟测工作流 YAML 解析通过；
- 异常栈、日志和仓库文件凭证扫描未发现实际凭证副本；
- 正式 Redis 缓存键为规范化参数 SHA-256，不包含原始地址；Redis 故障会旁路，不阻断供应商查询；
- 已修复 PostgreSQL 18 卷路径和 Compose CORS 环境变量解析问题。
- 2026-08-13 使用项目专属 Host 完成和风三日、24 小时和预警真实烟测，综合用例 1 项通过。

## 4. 尚未闭合的外部证据

1. 本地高德 Key 在一次失败烟测的工具回溯中出现过，必须先在高德控制台轮换；
2. 只有一个高德 Key 不影响轮换：在控制台重置或删除后重建该凭证，再更新本地 `AMAP_API_KEY`；
3. 轮换后需显式授权将新 Key 发送到 `https://restapi.amap.com`，执行 POI、地理编码、驾车和 V5 步行烟测；
4. 高德在线烟测通过并补录证据后，方可将第二阶段标记为完成。
