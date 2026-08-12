export type HealthState = "ok" | "ready" | "not_ready" | "unreachable";

export interface HealthResponse {
  status: HealthState;
  checks: Record<string, string>;
}

export interface SystemHealth {
  live: HealthResponse;
  ready: HealthResponse;
  checkedAt: string;
}

const apiBaseUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

async function fetchHealth(path: string): Promise<HealthResponse> {
  try {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    const payload = (await response.json()) as HealthResponse;
    return payload;
  } catch {
    return { status: "unreachable", checks: {} };
  }
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const [live, ready] = await Promise.all([
    fetchHealth("/health/live"),
    fetchHealth("/health/ready"),
  ]);
  return {
    live,
    ready,
    checkedAt: new Date().toISOString(),
  };
}
