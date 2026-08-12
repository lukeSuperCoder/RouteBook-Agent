# ADR-0001：业务版本与 LangGraph Checkpoint 分离

状态：Accepted（2026-08-12）

路书正式事实保存在 `routebook.routebook_versions` 的不可变 JSONB 快照中。LangGraph Checkpoint 只保存单次 Workflow Run 的恢复上下文，使用独立 `langgraph` schema，`thread_id` 等于 `workflow_run_id`。

页面、API 和最终分享能力只读取明确的业务版本。删除 checkpoint 不得改变任何已提交路书，重放 checkpoint 也不得绕过领域版本事务。
