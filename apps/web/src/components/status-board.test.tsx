import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBoard } from "./status-board";

describe("StatusBoard", () => {
  it("announces ready dependencies with readable labels", () => {
    render(
      <StatusBoard
        health={{
          live: { status: "ok", checks: { api: "ok" } },
          ready: {
            status: "ready",
            checks: {
              postgres: "ok",
              redis: "ok",
              migrations: "ok",
              checkpoint: "ok",
            },
          },
          checkedAt: "2026-08-12T08:00:00.000Z",
        }}
      />,
    );

    expect(screen.getByText("全线就绪")).toBeInTheDocument();
    expect(screen.getByText("LangGraph Checkpoint")).toBeInTheDocument();
    expect(screen.getAllByText("在线")).toHaveLength(5);
  });
});
