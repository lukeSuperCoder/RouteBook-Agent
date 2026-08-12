import { StatusBoard } from "@/components/status-board";
import { getSystemHealth } from "@/lib/api";

export default async function Home() {
  const health = await getSystemHealth();

  return (
    <main>
      <header className="masthead">
        <a className="wordmark" href="#top" aria-label="RouteBook Agent 首页">
          ROUTEBOOK<span>/</span>AGENT
        </a>
        <p>PHASE 01 · FOUNDATION</p>
      </header>

      <div id="top" className="hero-grid">
        <section className="hero-copy" aria-labelledby="page-title">
          <p className="route-code">RB / CN / 001</p>
          <h1 id="page-title">
            路书正在
            <br />
            <em>建立坐标</em>
          </h1>
          <p className="lede">
            第一阶段把流程的起点钉牢：每一次创建、调度与保存，都有清晰的状态、版本和恢复路径。
          </p>

          <div className="scope-note">
            <span aria-hidden="true">↗</span>
            <p>
              当前仅开放工程状态。
              <br />
              对话、地图与行程编辑将在后续阶段抵达。
            </p>
          </div>
        </section>

        <StatusBoard health={health} />
      </div>

      <footer>
        <p>MODULAR MONOLITH · IMMUTABLE VERSIONS · RECOVERABLE WORKFLOWS</p>
        <p>Shanghai / UTC+08</p>
      </footer>
    </main>
  );
}
