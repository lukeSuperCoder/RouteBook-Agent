import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouteBookWorkspace } from "./routebook-workspace";

describe("RouteBookWorkspace", () => {
  it("starts from a single travel brief and keeps submission disabled while empty", () => {
    render(<RouteBookWorkspace initialRouteBookId={null} />);

    const submit = screen.getByRole("button", { name: "开始规划 →" });
    expect(screen.getByRole("heading", { name: /把想去的地方/ })).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("描述你的旅行"), {
      target: { value: "从上海去南京三天，必去中山陵" },
    });
    expect(submit).toBeEnabled();
  });
});
