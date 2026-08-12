# ADR-0002：不可变版本与乐观并发事务

状态：Accepted（2026-08-12）

每次有效提交先插入完整快照，再以 `routebooks.current_version_id == base_version_id` 为条件切换当前版本，两步位于同一事务。条件更新失败时整体回滚并返回 `VERSION_CONFLICT`。

版本 Repository 不提供更新和删除操作；`(routebook_id, version_number)` 与 `workflow_run_id` 均唯一。撤销在后续阶段通过复制历史快照创建新版本实现。
