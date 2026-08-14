export type FactStatus = "verified" | "unverified" | "stale" | "unavailable" | "conflicted" | "proposed";

export interface RequirementValue<T> {
  value: T | null;
  source: string;
  confidence: number;
  confirmed: boolean;
}

export interface RouteBookSnapshot {
  schema_version: 1;
  requirements: Record<string, RequirementValue<unknown>>;
  places: Array<{
    id: string;
    name: string;
    address: string;
    district: string;
    longitude: number;
    latitude: number;
    semantic_type: string;
    status: FactStatus;
  }>;
  days_plan: Array<{
    day_number: number;
    date: string | null;
    place_ids: string[];
    segment_ids: string[];
    weather_refs: string[];
    notes: string[];
  }>;
  route_segments: Array<{
    id: string;
    origin_place_id: string;
    destination_place_id: string;
    mode: string;
    distance_meters: number | null;
    duration_seconds: number | null;
    status: FactStatus;
  }>;
  weather: Array<{ ref: string; place_id: string; status: FactStatus; payload: Record<string, unknown> }>;
  notes: string[];
  warnings: Array<Record<string, unknown>>;
}

export interface RouteBookVersion {
  id: string;
  version_number: number;
  parent_version_id: string | null;
  snapshot: RouteBookSnapshot;
  change_type: string;
  change_summary: string;
  created_at: string;
}

export interface SharedRouteBook {
  title: string;
  routebook_version_id: string;
  version_number: number;
  snapshot: RouteBookSnapshot;
  privacy_policy: "public" | "redact_addresses";
  created_at: string;
}

export interface RouteBook {
  id: string;
  title: string;
  status: string;
  current_version_id: string | null;
  latest_final_version_id: string | null;
  current_version: RouteBookVersion | null;
}

export interface ConversationMessage {
  id: string;
  workflow_run_id: string;
  message_id: string;
  role: "user" | "assistant" | "system";
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Proposal {
  id: string;
  base_version_id: string;
  preview_snapshot: RouteBookSnapshot;
  impact_scope: Record<string, unknown>;
  risk_flags: Array<Record<string, unknown>>;
  status: "pending" | "accepted" | "rejected" | "expired";
}

export interface RecommendationCandidate {
  id: string;
  name: string;
  type: string;
  address: string;
  district: string;
  recommendation_reason: string;
  transport_tradeoffs: string[];
  score: number;
  score_evidence: string[];
  status: "proposed" | "accepted" | "rejected" | "replaced";
}

export interface RecommendationBatch {
  id: string;
  base_version_id: string;
  candidates: RecommendationCandidate[];
}

export interface WorkflowAccepted {
  workflow_run_id: string;
  workflow_status: string;
  events_url: string;
}

export interface ProgressEvent {
  stage: string;
  status: string;
  message: string;
  progress: { completed: number; total: number };
}

export interface HealthResponse {
  status: "ok" | "ready" | "not_ready" | "unreachable";
  checks: Record<string, string>;
}

export interface SystemHealth {
  live: HealthResponse;
  ready: HealthResponse;
  checkedAt: string;
}

const browserApiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${browserApiBase}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new Error(body?.error?.message ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export const routeBookApi = {
  baseUrl: browserApiBase,
  create: (title: string) =>
    request<{ routebook_id: string }>("/api/routebooks", {
      method: "POST",
      headers: { "Idempotency-Key": `web-${crypto.randomUUID()}` },
      body: JSON.stringify({ title }),
    }),
  get: (id: string) => request<RouteBook>(`/api/routebooks/${id}`),
  messages: (id: string) => request<ConversationMessage[]>(`/api/routebooks/${id}/messages`),
  proposals: (id: string) => request<Proposal[]>(`/api/routebooks/${id}/proposals`),
  versions: (id: string) => request<RouteBookVersion[]>(`/api/routebooks/${id}/versions`),
  sendMessage: (id: string, text: string) =>
    request<WorkflowAccepted>(`/api/routebooks/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ message_id: `web-${crypto.randomUUID()}`, text }),
    }),
  recommendations: (id: string) =>
    request<RecommendationBatch>(`/api/routebooks/${id}/recommendations/latest`),
  generateRecommendations: (id: string) =>
    request<RecommendationBatch>(`/api/routebooks/${id}/recommendations`, {
      method: "POST",
      body: JSON.stringify({ limit: 8 }),
    }),
  feedback: (
    routebookId: string,
    proposalId: string,
    action: "accept" | "reject" | "replace",
    reason?: "too_far" | "not_interested" | "already_visited" | "too_crowded" | "other",
  ) => request<RecommendationBatch>(
    `/api/routebooks/${routebookId}/recommendations/${proposalId}/feedback`,
    { method: "POST", body: JSON.stringify({ action, reason: reason ?? null }) },
  ),
  generateItinerary: (id: string) =>
    request<{ feasible: boolean; version_id: string | null; conflicts: Array<Record<string, unknown>> }>(
      `/api/routebooks/${id}/itinerary`,
      { method: "POST" },
    ),
  editDay: (id: string, dayNumber: number, note: string) =>
    request<{ status: string; version_id: string | null; proposal: Proposal | null }>(
      `/api/routebooks/${id}/edits`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: crypto.randomUUID(),
          operation: "edit_day",
          day_reference: `第${dayNumber}天`,
          note,
        }),
      },
    ),
  resume: (runId: string, text: string) =>
    request<WorkflowAccepted>(`/api/workflow-runs/${runId}/resume`, {
      method: "POST",
      body: JSON.stringify({
        interrupt_kind: "requirement_clarification",
        message_id: `web-${crypto.randomUUID()}`,
        text,
      }),
    }),
  decide: (proposalId: string, decision: "accept" | "reject") =>
    request<{ version_id: string | null }>(`/api/proposals/${proposalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  undo: (id: string) =>
    request<{ version_id: string }>(`/api/routebooks/${id}/undo`, {
      method: "POST",
      body: JSON.stringify({ operation_id: crypto.randomUUID() }),
    }),
  finalize: (id: string, versionId: string) =>
    request<{ share_url: string; public_token: string }>(`/api/routebooks/${id}/finalize`, {
      method: "POST",
      body: JSON.stringify({
        routebook_version_id: versionId,
        privacy_policy: "redact_addresses",
      }),
    }),
};

export async function getSharedRouteBook(token: string): Promise<SharedRouteBook | null> {
  const serverApiBase = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  const response = await fetch(`${serverApiBase}/share/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (!response.ok) return null;
  return response.json() as Promise<SharedRouteBook>;
}
