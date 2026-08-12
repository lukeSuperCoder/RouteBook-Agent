import type { SystemHealth } from "@/lib/api";

const labels: Record<string, string> = {
  api: "API",
  postgres: "PostgreSQL",
  redis: "Redis",
  migrations: "业务迁移",
  checkpoint: "LangGraph Checkpoint",
};

const order = ["api", "postgres", "redis", "migrations", "checkpoint"];

function stateLabel(value: string): string {
  if (value === "ok") return "在线";
  if (value === "missing") return "待初始化";
  if (value === "unavailable") return "不可用";
  if (value === "unknown") return "未知";
  return "未连接";
}

export function StatusBoard({ health }: { health: SystemHealth }) {
  const checks = { ...health.ready.checks, ...health.live.checks };
  const isReady = health.ready.status === "ready";

  return (
    <section className="status-board" aria-labelledby="status-title" aria-live="polite">
      <div className="status-heading">
        <div>
          <p className="eyebrow">SYSTEM READINESS</p>
          <h2 id="status-title">基础设施状态</h2>
        </div>
        <p className={`readiness ${isReady ? "ready" : "pending"}`}>
          <span aria-hidden="true" />
          {isReady ? "全线就绪" : "等待依赖"}
        </p>
      </div>

      <ol className="status-list">
        {order.map((key, index) => {
          const state = checks[key] ?? "unreachable";
          return (
            <li key={key} className="status-row">
              <span className="sequence" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="service-name">{labels[key]}</span>
              <span className={`service-state state-${state}`}>
                <span className="state-mark" aria-hidden="true" />
                {stateLabel(state)}
              </span>
            </li>
          );
        })}
      </ol>

      <p className="checked-at">
        最近检查：<time dateTime={health.checkedAt}>{health.checkedAt}</time>
      </p>
    </section>
  );
}
