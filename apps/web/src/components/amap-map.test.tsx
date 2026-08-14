import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AmapMap } from "./amap-map";

describe("AmapMap", () => {
  it("keeps the coordinate fallback when no browser map key is configured", () => {
    render(
      <AmapMap
        places={[]}
        selectedPlaceId={null}
        onSelect={vi.fn()}
        fallback={<p>坐标降级地图</p>}
      />,
    );

    expect(screen.getByText("坐标降级地图")).toBeVisible();
    expect(screen.queryByText("正在加载高德地图…")).not.toBeInTheDocument();
  });
});
