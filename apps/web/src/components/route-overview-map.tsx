"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import type { RouteBookSnapshot } from "@/lib/api";

const AmapMap = dynamic(
  () => import("@/components/amap-map").then((module) => module.AmapMap),
  { ssr: false, loading: () => <p className="map-loading" role="status">正在准备全程地图…</p> },
);

type Place = RouteBookSnapshot["places"][number];
type DayPlan = RouteBookSnapshot["days_plan"][number];
type Segment = RouteBookSnapshot["route_segments"][number];

function formatDuration(seconds: number | null | undefined) {
  if (!seconds) return "耗时待定";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `约 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `约 ${hours} 小时${rest ? ` ${rest} 分钟` : ""}`;
}

function CoordinateOverview({ places, selectedPlaceId, onSelect }: {
  places: Place[];
  selectedPlaceId: string | null;
  onSelect: (id: string) => void;
}) {
  const lng = places.map((item) => item.longitude);
  const lat = places.map((item) => item.latitude);
  const minLng = Math.min(...lng), maxLng = Math.max(...lng);
  const minLat = Math.min(...lat), maxLat = Math.max(...lat);
  const point = (value: number, min: number, max: number) => max === min ? 50 : 10 + ((value - min) / (max - min)) * 80;

  return <div className="coordinate-map">
    <svg viewBox="0 0 100 100" role="img" aria-label={`完整行程路线，共 ${places.length} 个地点`}>
      <polyline points={places.map((place) => `${point(place.longitude, minLng, maxLng)},${100 - point(place.latitude, minLat, maxLat)}`).join(" ")} />
    </svg>
    {places.map((place, index) => <button
      type="button"
      key={place.id}
      className={`map-marker ${place.status} ${selectedPlaceId === place.id ? "selected" : ""}`}
      style={{ left: `${point(place.longitude, minLng, maxLng)}%`, top: `${100 - point(place.latitude, minLat, maxLat)}%` }}
      onClick={() => onSelect(place.id)}
      aria-label={`第 ${index + 1} 站 ${place.name}`}
    >{index + 1}<span>{place.name}</span></button>)}
  </div>;
}

export function RouteOverviewMap({ places, days, segments }: { places: Place[]; days: DayPlan[]; segments: Segment[] }) {
  const [selectedPlaceId, setSelectedPlaceId] = useState<string | null>(null);
  const [activeDay, setActiveDay] = useState(days[0]?.day_number ?? 1);
  const placeById = useMemo(() => new Map(places.map((place) => [place.id, place])), [places]);
  const selectedPlace = placeById.get(selectedPlaceId ?? "") ?? null;
  const totalDistance = segments.reduce((sum, segment) => sum + (segment.distance_meters ?? 0), 0);

  if (!places.length) return <div className="share-map-empty">暂无可展示的路线坐标</div>;

  return <div className="route-map-explorer">
    <aside className="route-day-panel" aria-label="分日行程路线">
      <header><p>全程路线</p><strong>{days.length} 天 · {places.length} 个地点</strong><span>{totalDistance ? `约 ${(totalDistance / 1000).toFixed(1)} 公里` : "距离将在路线确认后更新"}</span></header>
      <div className="route-day-list">
        {days.map((day) => {
          const dayPlaces = day.place_ids.flatMap((id) => placeById.get(id) ?? []);
          const expanded = activeDay === day.day_number;
          return <section className={expanded ? "active" : ""} key={day.day_number}>
            <button className="route-day-toggle" type="button" aria-expanded={expanded} onClick={() => setActiveDay(day.day_number)}><span>第 {day.day_number} 天</span><strong>{day.date ?? "日期待定"}</strong><i>{expanded ? "−" : "+"}</i></button>
            {expanded && <ol>{dayPlaces.map((place, index) => {
              const segment = segments.find((item) => item.origin_place_id === place.id);
              return <li key={place.id} className={selectedPlaceId === place.id ? "selected" : ""}>
                <button type="button" onClick={() => setSelectedPlaceId(place.id)}><b><i>{index + 1}</i></b><span><strong>{place.name}</strong><small>{place.district || place.semantic_type}</small></span></button>
                {segment && <p><span aria-hidden="true">🚗</span>{segment.distance_meters ? `约 ${(segment.distance_meters / 1000).toFixed(1)} 公里` : "距离待定"} · {formatDuration(segment.duration_seconds)}</p>}
              </li>;
            })}</ol>}
          </section>;
        })}
      </div>
    </aside>
    <div className="share-map-canvas">
      <AmapMap places={places} selectedPlaceId={selectedPlaceId} onSelect={setSelectedPlaceId} fallback={<CoordinateOverview places={places} selectedPlaceId={selectedPlaceId} onSelect={setSelectedPlaceId} />} />
      <div className="route-map-tools" aria-label="地图提示"><span aria-hidden="true">⌖</span> 点击编号查看地点</div>
      {selectedPlace && <article className="route-place-card" aria-live="polite"><button type="button" onClick={() => setSelectedPlaceId(null)} aria-label="关闭地点详情">×</button><span className="route-place-index">{places.findIndex((place) => place.id === selectedPlace.id) + 1}</span><div><small>{selectedPlace.district || "行程地点"}</small><strong>{selectedPlace.name}</strong><p>{selectedPlace.address || selectedPlace.semantic_type}</p></div></article>}
    </div>
  </div>;
}
