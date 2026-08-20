import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/route-overview-map", () => ({
  RouteOverviewMap: () => <div data-testid="route-overview-map" />,
}));

import { SharedRouteBookView } from "./shared-routebook";

describe("SharedRouteBookView", () => {
  it("renders only the supplied immutable version snapshot", () => {
    render(
      <SharedRouteBookView
        routebook={{
          title: "南京三日路书",
          routebook_id: "routebook-1234",
          routebook_version_id: "version-fixed-1234",
          version_number: 4,
          privacy_policy: "redact_addresses",
          created_at: "2026-08-13T08:00:00Z",
          snapshot: {
            schema_version: 1,
            requirements: {
              destination: {
                value: "南京",
                source: "explicit",
                confidence: 1,
                confirmed: true,
              },
            },
            places: [{
              id: "p1",
              name: "中山陵",
              address: "",
              district: "玄武区",
              longitude: 118.85,
              latitude: 32.06,
              semantic_type: "attraction",
              status: "verified",
            }],
            days_plan: [{
              day_number: 1,
              date: "2026-09-01",
              place_ids: ["p1"],
              segment_ids: [],
              weather_refs: [],
              notes: [],
            }],
            route_segments: [],
            weather: [],
            notes: [],
            warnings: [],
          },
        }}
      />,
    );

    expect(screen.getByText("固定版本 v4 · 2026/8/13")).toBeInTheDocument();
    expect(screen.getByText("中山陵")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← 返回继续调整" })).toHaveAttribute("href", "/?routebook=routebook-1234");
    expect(screen.getByRole("heading", { name: "全程路线地图" })).toBeInTheDocument();
    expect(screen.getByTestId("route-overview-map")).toBeInTheDocument();
    expect(screen.getByText("精确地址已隐藏")).toBeInTheDocument();
  });
});
