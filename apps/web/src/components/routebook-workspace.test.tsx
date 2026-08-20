import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouteBookWorkspace } from "./routebook-workspace";

describe("RouteBookWorkspace", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("starts from a single travel brief and keeps submission disabled while empty", () => {
    render(<RouteBookWorkspace initialRouteBookId={null} />);

    const submit = screen.getByRole("button", { name: "开始规划 →" });
    expect(screen.getByRole("heading", { name: /输入旅行需求/ })).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("描述你的旅行"), {
      target: { value: "从上海去南京三天，必去中山陵" },
    });
    expect(submit).toBeEnabled();
  });

  it("uses a planning task panel and keeps the map unmounted before an itinerary exists", async () => {
    const snapshot = {
      schema_version: 1,
      requirements: {},
      places: [],
      days_plan: [],
      route_segments: [],
      weather: [],
      notes: [],
      warnings: [],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/workflow-runs/active")) return Response.json([]);
      if (url.endsWith("/recommendations/latest")) return new Response("{}", { status: 404 });
      if (url.endsWith("/messages") || url.endsWith("/proposals")) return Response.json([]);
      if (url.endsWith("/versions")) return Response.json([{ id: "version-1", version_number: 1, parent_version_id: null, snapshot, change_type: "create", change_summary: "建立路书", created_at: "2026-08-17T00:00:00Z" }]);
      return Response.json({ id: "routebook-1", title: "北京三日游", status: "draft", current_version_id: "version-1", latest_final_version_id: null, current_version: { id: "version-1", version_number: 1, parent_version_id: null, snapshot, change_type: "create", change_summary: "建立路书", created_at: "2026-08-17T00:00:00Z" } });
    }));

    render(<RouteBookWorkspace initialRouteBookId="routebook-1" />);

    expect(await screen.findByRole("heading", { name: "核对旅行需求", level: 2 })).toBeInTheDocument();
    expect(screen.getByLabelText("规划对话")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /路线地图/ })).not.toBeInTheDocument();
    expect(screen.queryByText("查看地图 ↗")).not.toBeInTheDocument();
  });

  it("renders structured clarification choices as actionable controls", async () => {
    const snapshot = { schema_version: 1, requirements: {}, places: [], days_plan: [], route_segments: [], weather: [], notes: [], warnings: [] };
    const version = { id: "version-1", version_number: 1, parent_version_id: null, snapshot, change_type: "create", change_summary: "建立路书", created_at: "2026-08-17T00:00:00Z" };
    let eventSourceCount = 0;
    vi.stubGlobal("EventSource", class {
      constructor() { eventSourceCount += 1; }
      addEventListener() {}
      close() {}
      set onerror(_handler: unknown) {}
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/resume")) return Response.json({ workflow_run_id: "run-1", workflow_status: "queued", status_url: "/runs/run-1", events_url: "/runs/run-1/events" }, { status: 202 });
      if (url.endsWith("/workflow-runs/active")) return Response.json([]);
      if (url.endsWith("/recommendations/latest")) return new Response("{}", { status: 404 });
      if (url.endsWith("/messages")) return Response.json([{
        id: "assistant-1",
        workflow_run_id: "run-1",
        message_id: "message-1",
        role: "assistant",
        kind: "requirement_clarification",
        created_at: "2026-08-17T00:00:00Z",
        payload: {
          requirements: {
            destination: { value: "北京", source: "explicit", confidence: 1, confirmed: true, decision_status: "confirmed", suggestion_reason: null },
            days: { value: 3, source: "explicit", confidence: 1, confirmed: true, decision_status: "confirmed", suggestion_reason: null },
          },
          questions: [{
            question_id: "clarify-trip-scope",
            issue_code: "missing_trip_scope",
            fields: ["trip_scope"],
            prompt: "这次需要把出发地和往返交通也规划进去吗？",
            input_type: "single_choice",
            options: [
              { value: "只规划目的地内部行程，不考虑往返", label: "只规划目的地内部", description: "不需要填写出发城市", recommended: true, recommendation_reason: "当前需求更适合先规划市内路线。" },
              { value: "包含往返交通，我会补充出发地", label: "包含出发地和往返" },
          ],
          rationale: "是否包含往返会改变后续问题。",
          }],
        },
      }]);
      if (url.endsWith("/proposals")) return Response.json([]);
      if (url.endsWith("/versions")) return Response.json([version]);
      return Response.json({ id: "routebook-1", title: "北京三日游", status: "draft", current_version_id: "version-1", latest_final_version_id: null, current_version: version });
    }));

    render(<RouteBookWorkspace initialRouteBookId="routebook-1" />);

    expect(await screen.findByRole("heading", { name: "把关键条件确认清楚" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /只规划目的地内部/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /包含出发地和往返/ }).length).toBeGreaterThan(0);
    expect(screen.getByText(/为什么现在问/)).toBeInTheDocument();
    expect(screen.getAllByText("建议").length).toBeGreaterThan(0);
    expect(screen.getByText("北京")).toBeInTheDocument();
    expect(screen.getByText("3 天")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "生成地点推荐" })).not.toBeInTheDocument());

    expect(eventSourceCount).toBe(1);
    fireEvent.click(screen.getAllByRole("button", { name: /只规划目的地内部/ })[0]);
    await waitFor(() => expect(eventSourceCount).toBe(2));
  });

  it("announces confirmed requirements and automatically starts place recommendations", async () => {
    const snapshot = { schema_version: 1, requirements: {}, places: [], days_plan: [], route_segments: [], weather: [], notes: [], warnings: [] };
    const version = { id: "version-ready", version_number: 2, parent_version_id: "version-1", snapshot, change_type: "edit", change_summary: "更新旅行需求", created_at: "2026-08-18T00:00:00Z" };
    vi.stubGlobal("EventSource", class {
      addEventListener() {}
      close() {}
      set onerror(_handler: unknown) {}
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/recommendations") && init?.method === "POST") {
        return Response.json({ workflow_run_id: "recommend-run", workflow_status: "queued", status_url: "/runs/recommend-run", events_url: "/runs/recommend-run/events" }, { status: 202 });
      }
      if (url.endsWith("/workflow-runs/active")) return Response.json([]);
      if (url.endsWith("/recommendations/latest")) return new Response("{}", { status: 404 });
      if (url.endsWith("/messages")) return Response.json([{
        id: "assistant-ready",
        workflow_run_id: "requirement-run",
        message_id: "message-ready",
        role: "assistant",
        kind: "status",
        created_at: "2026-08-18T00:00:00Z",
        payload: { text: "需求已确认，请在右侧查看地点推荐。" },
      }]);
      if (url.endsWith("/proposals")) return Response.json([]);
      if (url.endsWith("/versions")) return Response.json([version]);
      return Response.json({ id: "routebook-1", title: "北京三日游", status: "planning", current_version_id: version.id, latest_final_version_id: null, current_version: version });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<RouteBookWorkspace initialRouteBookId="routebook-1" />);

    expect(await screen.findByText("需求已确认，请在右侧查看地点推荐。")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/recommendations$/),
      expect.objectContaining({ method: "POST" }),
    ));
    expect((await screen.findAllByText("需求已确认，正在生成地点推荐…")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "选择想去的地方", level: 2 })).toBeInTheDocument();
  });
});
