"use client";

import { useEffect, useRef, useState } from "react";
import type { RouteBookSnapshot } from "@/lib/api";

type Place = RouteBookSnapshot["places"][number];
type MapInstance = { destroy(): void; setFitView(overlays?: unknown[], immediately?: boolean, avoid?: number[]): void; setMapStyle(style: string): void };
type MarkerInstance = { on(event: "click", listener: () => void): void; setMap(map: null): void };
type DrivingInstance = { search(origin: [number, number], destination: [number, number], callback: (status: string) => void): void };
type AMapGlobal = {
  Map: new (container: HTMLDivElement, options: Record<string, unknown>) => MapInstance;
  Marker: new (options: Record<string, unknown>) => MarkerInstance;
  Polyline: new (options: Record<string, unknown>) => unknown;
  Driving?: new (options: Record<string, unknown>) => DrivingInstance;
};

declare global {
  interface Window {
    AMap?: AMapGlobal;
    _AMapSecurityConfig?: { securityJsCode: string };
  }
}

let amapPromise: Promise<AMapGlobal> | null = null;

function loadAmap(key: string, securityCode?: string): Promise<AMapGlobal> {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (amapPromise) return amapPromise;
  if (securityCode) window._AMapSecurityConfig = { securityJsCode: securityCode };
  amapPromise = new Promise((resolve, reject) => {
    const callback = `routebookAmapReady_${Date.now()}`;
    const timeout = window.setTimeout(() => {
      delete (window as unknown as Record<string, unknown>)[callback];
      amapPromise = null;
      reject(new Error("AMap SDK load timeout"));
    }, 8000);
    (window as unknown as Record<string, unknown>)[callback] = () => {
      window.clearTimeout(timeout);
      delete (window as unknown as Record<string, unknown>)[callback];
      if (window.AMap) resolve(window.AMap);
      else reject(new Error("AMap SDK unavailable"));
    };
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}&plugin=AMap.Driving&callback=${callback}`;
    script.async = true;
    script.dataset.routebookAmap = "true";
    script.setAttribute("fetchpriority", "low");
    script.onerror = () => {
      window.clearTimeout(timeout);
      delete (window as unknown as Record<string, unknown>)[callback];
      script.remove();
      amapPromise = null;
      reject(new Error("AMap SDK failed"));
    };
    document.head.append(script);
  });
  return amapPromise;
}

export function AmapMap({ places, selectedPlaceId, onSelect, fallback }: {
  places: Place[];
  selectedPlaceId: string | null;
  onSelect: (id: string) => void;
  fallback: React.ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"fallback" | "loading" | "ready" | "failed">(
    process.env.NEXT_PUBLIC_AMAP_JS_KEY ? "loading" : "fallback",
  );

  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_AMAP_JS_KEY;
    const container = containerRef.current;
    if (!key || !container || !places.length) return;
    let disposed = false;
    let map: MapInstance | null = null;
    const markers: MarkerInstance[] = [];
    loadAmap(key, process.env.NEXT_PUBLIC_AMAP_SECURITY_CODE).then((AMap) => {
      if (disposed) return;
      map = new AMap.Map(container, {
        zoom: 12,
        center: [places[0].longitude, places[0].latitude],
        mapStyle: "amap://styles/normal",
        viewMode: "2D",
        showLabel: true,
      });
      places.forEach((place, index) => {
        const markerContent = document.createElement("button");
        markerContent.type = "button";
        markerContent.className = `amap-route-marker${selectedPlaceId === place.id ? " selected" : ""}`;
        markerContent.setAttribute("aria-label", `第 ${index + 1} 站 ${place.name}`);
        markerContent.innerHTML = `<span>${index + 1}</span>`;
        const marker = new AMap.Marker({
          map,
          position: [place.longitude, place.latitude],
          title: place.name,
          content: markerContent,
          anchor: "bottom-center",
          zIndex: selectedPlaceId === place.id ? 130 : 100,
        });
        marker.on("click", () => onSelect(place.id));
        markers.push(marker);
      });
      if (places.length > 1) {
        const drawFallback = (origin: Place, destination: Place) => {
          const path = [[origin.longitude, origin.latitude], [destination.longitude, destination.latitude]];
          new AMap.Polyline({ map, path, strokeColor: "#ffffff", strokeWeight: 10, strokeOpacity: 0.92, lineJoin: "round", lineCap: "round", zIndex: 48 });
          new AMap.Polyline({ map, path, strokeColor: "#1688dc", strokeWeight: 6, strokeOpacity: 0.9, lineJoin: "round", lineCap: "round", zIndex: 50 });
        };
        const Driving = AMap.Driving;
        if (Driving) {
          let completed = 0;
          places.slice(0, -1).forEach((origin, index) => {
            const destination = places[index + 1];
            const driving = new Driving({ map, hideMarkers: true, showTraffic: false, autoFitView: false, outlineColor: "#ffffff" });
            driving.search([origin.longitude, origin.latitude], [destination.longitude, destination.latitude], (status) => {
              if (status !== "complete") drawFallback(origin, destination);
              completed += 1;
              if (completed === places.length - 1) map?.setFitView(undefined, false, [88, 64, 88, 64]);
            });
          });
        } else {
          places.slice(0, -1).forEach((origin, index) => drawFallback(origin, places[index + 1]));
          map.setFitView(undefined, false, [88, 64, 88, 64]);
        }
      }
      setState("ready");
    }).catch(() => {
      if (!disposed) setState("failed");
    });
    return () => {
      disposed = true;
      markers.forEach((marker) => marker.setMap(null));
      map?.destroy();
    };
  }, [onSelect, places, selectedPlaceId]);

  return <div className="map-stack">
    <div className={state === "ready" ? "map-fallback hidden" : "map-fallback"}>{fallback}</div>
    {state === "loading" && <p className="map-loading" role="status">正在加载高德地图…</p>}
    {state === "failed" && <p className="map-degraded">高德地图暂不可用 · 已切换坐标视图</p>}
    <div ref={containerRef} className={state === "ready" ? "amap-canvas ready" : "amap-canvas"} aria-hidden={state !== "ready"} />
  </div>;
}
