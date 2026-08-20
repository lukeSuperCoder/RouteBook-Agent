import Link from "next/link";
import { RouteOverviewMap } from "@/components/route-overview-map";
import type { SharedRouteBook } from "@/lib/api";

export function SharedRouteBookView({ routebook }: { routebook: SharedRouteBook }) {
  const { snapshot } = routebook;
  const places = new Map(snapshot.places.map((place) => [place.id, place]));
  const orderedPlaces = snapshot.days_plan.flatMap((day) => day.place_ids.flatMap((id) => places.get(id) ?? []));
  return (
    <main className="share-shell">
      <header className="share-header">
        <b>ROUTEBOOK<span>/</span>AGENT</b>
        <div><p>固定版本 v{routebook.version_number} · {new Date(routebook.created_at).toLocaleDateString("zh-CN")}</p><Link href={`/?routebook=${routebook.routebook_id}`}>← 返回继续调整</Link></div>
      </header>
      <section className="share-hero">
        <p className="kicker">A JOURNEY, HELD IN PLACE</p>
        <h1>{routebook.title}</h1>
        <div><span>目的地<strong>{String(snapshot.requirements.destination?.value ?? "待定")}</strong></span><span>天数<strong>{String(snapshot.requirements.days?.value ?? snapshot.days_plan.length)} 天</strong></span><span>节奏<strong>{String(snapshot.requirements.intensity?.value ?? "适中")}</strong></span></div>
      </section>
      <section className="share-map-section" aria-labelledby="route-map-title">
        <div className="share-map-heading"><div><p className="kicker">FULL ROUTE / MAP</p><h2 id="route-map-title">全程路线地图</h2></div><p>按每日行程顺序连接全部 {orderedPlaces.length} 个地点</p></div>
        <RouteOverviewMap places={orderedPlaces} days={snapshot.days_plan} segments={snapshot.route_segments} />
      </section>
      <section className="share-days">
        {snapshot.days_plan.map((day) => <article key={day.day_number}>
          <header><span>DAY {String(day.day_number).padStart(2, "0")}</span><h2>{day.date ?? `第 ${day.day_number} 天`}</h2></header>
          <ol>{day.place_ids.map((id, index) => { const place = places.get(id); return place ? <li key={id}><i>{index + 1}</i><div><strong>{place.name}</strong><p>{place.district}{place.address ? ` · ${place.address}` : ""}</p></div><small>{place.status}</small></li> : null; })}</ol>
          {day.notes.map((note) => <p className="day-note" key={note}>{note}</p>)}
        </article>)}
      </section>
      <footer className="share-footer"><p>此页面永久绑定版本 {routebook.routebook_version_id.slice(0, 8)}</p><p>{routebook.privacy_policy === "redact_addresses" ? "精确地址已隐藏" : "公开视图"}</p></footer>
    </main>
  );
}
