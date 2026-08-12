export default function Loading() {
  return (
    <main className="loading-shell" aria-live="polite" aria-busy="true">
      <p className="eyebrow">SYSTEM READINESS</p>
      <h1>正在校准运行坐标…</h1>
      <div className="loading-line" aria-hidden="true" />
    </main>
  );
}
