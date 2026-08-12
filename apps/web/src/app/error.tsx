"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="error-shell">
      <p className="eyebrow">ROUTE INTERRUPTED</p>
      <h1>状态页暂时偏离路线</h1>
      <p>工程服务没有受到修改。可以重新读取当前状态。</p>
      <button type="button" onClick={reset}>
        重新检查
      </button>
    </main>
  );
}
