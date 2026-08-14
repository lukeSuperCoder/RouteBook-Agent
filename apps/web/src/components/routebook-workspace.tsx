"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AmapMap } from "@/components/amap-map";
import {
  type ConversationMessage,
  type FactStatus,
  type ProgressEvent,
  type RecommendationBatch,
  type Proposal,
  type RouteBook,
  type RouteBookVersion,
  type RouteBookSnapshot,
  routeBookApi,
} from "@/lib/api";

const statusCopy: Record<FactStatus, string> = {
  verified: "已验证",
  unverified: "未验证",
  stale: "已过期",
  unavailable: "不可用",
  conflicted: "有冲突",
  proposed: "提案预览",
};

function messageText(message: ConversationMessage): string {
  const payload = message.payload;
  if (Array.isArray(payload.questions)) {
    const prompts = payload.questions.flatMap((question) => {
      if (typeof question !== "object" || question === null) return [];
      const prompt = (question as Record<string, unknown>).prompt;
      return typeof prompt === "string" ? [prompt] : [];
    });
    if (prompts.length) return prompts.join("\n");
  }
  for (const key of ["text", "message", "question", "content"]) {
    if (typeof payload[key] === "string") return payload[key] as string;
  }
  return message.kind === "requirement_clarification" ? "还需要补充一些信息。" : "状态已更新";
}

function requirementText(snapshot: RouteBookSnapshot | null, key: string): string {
  const value = snapshot?.requirements[key]?.value;
  if (Array.isArray(value)) return value.length ? value.join("、") : "未设置";
  if (typeof value === "boolean") return value ? "接受" : "不接受";
  return value == null || value === "" ? "未设置" : String(value);
}

export function RouteBookWorkspace({ initialRouteBookId }: { initialRouteBookId: string | null }) {
  const [routebookId, setRoutebookId] = useState(initialRouteBookId);
  const [routebook, setRoutebook] = useState<RouteBook | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [versions, setVersions] = useState<RouteBookVersion[]>([]);
  const [recommendations, setRecommendations] = useState<RecommendationBatch | null>(null);
  const [preview, setPreview] = useState<Proposal | null>(null);
  const [displayedVersion, setDisplayedVersion] = useState<RouteBookVersion | null>(null);
  const [selectedPlaceId, setSelectedPlaceId] = useState<string | null>(null);
  const [activeDay, setActiveDay] = useState(1);
  const [draft, setDraft] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (id: string) => {
    const [book, thread, changes, history, latestRecommendations] = await Promise.all([
      routeBookApi.get(id),
      routeBookApi.messages(id),
      routeBookApi.proposals(id),
      routeBookApi.versions(id),
      routeBookApi.recommendations(id).catch(() => null),
    ]);
    setRoutebook(book);
    setMessages(thread);
    setProposals(changes);
    setVersions(history);
    setRecommendations(latestRecommendations);
    setDisplayedVersion((current) => history.find((item) => item.id === current?.id) ?? null);
    setPreview((current) => changes.find((item) => item.id === current?.id) ?? null);
    const latestMessage = thread.at(-1);
    const latestClarification = latestMessage?.role === "assistant"
      && latestMessage.kind === "requirement_clarification"
      ? latestMessage
      : null;
    if (latestClarification) {
      setRunId(latestClarification.workflow_run_id);
      setProgress((current) => current ?? {
        stage: "waiting_for_clarification",
        status: "interrupted",
        message: "等待你补充信息",
        progress: { completed: 0, total: 1 },
      });
    }
  }, []);

  useEffect(() => {
    if (!routebookId) return;
    const timer = window.setTimeout(() => {
      refresh(routebookId).catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "无法载入路书"),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh, routebookId]);

  useEffect(() => {
    if (!runId || !routebookId) return;
    const source = new EventSource(`${routeBookApi.baseUrl}/api/workflow-runs/${runId}/events`);
    const receive = (event: MessageEvent<string>) => {
      const next = JSON.parse(event.data) as ProgressEvent;
      setProgress(next);
      if (["completed", "failed", "interrupted"].includes(next.status)) {
        source.close();
        refresh(routebookId).catch(() => undefined);
      }
    };
    source.addEventListener("progress", receive as EventListener);
    source.onerror = () => setProgress((current) => current ?? {
      stage: "reconnecting",
      status: "running",
      message: "正在重新连接…",
      progress: { completed: 0, total: 1 },
    });
    return () => source.close();
  }, [refresh, routebookId, runId]);

  const officialSnapshot = displayedVersion?.snapshot ?? routebook?.current_version?.snapshot ?? null;
  const snapshot = preview?.preview_snapshot ?? officialSnapshot;
  const isHistorical = displayedVersion !== null;
  const day = snapshot?.days_plan.find((item) => item.day_number === activeDay);
  const places = useMemo(() => {
    const ids = new Set(day?.place_ids ?? []);
    return snapshot?.places.filter((place) => ids.has(place.id)) ?? [];
  }, [day, snapshot]);

  async function createTrip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await routeBookApi.create(draft.slice(0, 30));
      const accepted = await routeBookApi.sendMessage(created.routebook_id, draft);
      history.replaceState(null, "", `?routebook=${created.routebook_id}`);
      setRoutebookId(created.routebook_id);
      setRunId(accepted.workflow_run_id);
      setDraft("");
      await refresh(created.routebook_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!routebookId || !draft.trim()) return;
    setBusy(true);
    try {
      if (officialSnapshot?.days_plan.length) {
        const result = await routeBookApi.editDay(routebookId, activeDay, draft);
        if (result.proposal) setPreview(result.proposal);
      } else {
        const accepted = runId && progress?.status === "interrupted"
          ? await routeBookApi.resume(runId, draft)
          : await routeBookApi.sendMessage(routebookId, draft);
        setRunId(accepted.workflow_run_id);
      }
      setDraft("");
      await refresh(routebookId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "消息发送失败");
    } finally {
      setBusy(false);
    }
  }

  async function decide(proposal: Proposal, decision: "accept" | "reject") {
    if (!routebookId) return;
    setBusy(true);
    try {
      await routeBookApi.decide(proposal.id, decision);
      setPreview(null);
      await refresh(routebookId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提案处理失败");
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    if (!routebookId || !routebook?.current_version_id) return;
    setBusy(true);
    try {
      const result = await routeBookApi.finalize(routebookId, routebook.current_version_id);
      window.location.assign(result.share_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "最终页面生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function generateRecommendations() {
    if (!routebookId) return;
    setBusy(true);
    setError(null);
    try {
      setRecommendations(await routeBookApi.generateRecommendations(routebookId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "推荐生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function feedback(
    proposalId: string,
    action: "accept" | "reject",
    reason?: "not_interested" | "too_far",
  ) {
    if (!routebookId) return;
    setBusy(true);
    try {
      setRecommendations(await routeBookApi.feedback(routebookId, proposalId, action, reason));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "候选处理失败");
    } finally {
      setBusy(false);
    }
  }

  async function generateItinerary() {
    if (!routebookId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await routeBookApi.generateItinerary(routebookId);
      if (!result.feasible) {
        setError("当前约束无法生成连续行程，请调整必去地点或旅行节奏。");
      } else {
        await refresh(routebookId);
        setRecommendations(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "行程生成失败");
    } finally {
      setBusy(false);
    }
  }

  if (!routebookId) {
    return (
      <main className="welcome-shell">
        <header className="topbar"><b>ROUTEBOOK<span>/</span>AGENT</b><small>路线不是清单，是一天的呼吸。</small></header>
        <section className="welcome-card">
          <p className="kicker">从一句话开始 · PHASE 07</p>
          <h1>把想去的地方，<em>排成走得通的一天。</em></h1>
          <p>告诉我从哪里出发、去哪里、玩几天，以及你绝不能错过的地方。</p>
          <form className="starter" onSubmit={createTrip}>
            <label htmlFor="trip-brief">描述你的旅行</label>
            <textarea id="trip-brief" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="例如：9 月从上海自驾去南京三天，想看建筑和梧桐，必去中山陵，不想赶早。" />
            <button disabled={busy || !draft.trim()}>{busy ? "正在建立坐标…" : "开始规划 →"}</button>
          </form>
          {error && <p className="inline-error" role="alert">{error}</p>}
        </section>
      </main>
    );
  }

  return (
    <main className="workspace-shell">
      <header className="workspace-header">
        <Link href="/" className="brand">ROUTEBOOK<span>/</span>AGENT</Link>
        <div className="trip-title"><strong>{routebook?.title ?? "正在载入路书"}</strong><small>{routebook?.status ?? "loading"} · 固定读取版本 {routebook?.current_version?.version_number ?? "—"}</small></div>
        <div className="header-actions">
          <span className={`sync-state ${progress?.status ?? "idle"}`}>{progress?.message ?? "版本已同步"}</span>
          {isHistorical && <button onClick={() => setDisplayedVersion(null)}>返回当前版本</button>}
          <button disabled={busy || isHistorical || !routebook?.current_version?.parent_version_id} onClick={async () => { if (routebookId) { await routeBookApi.undo(routebookId); await refresh(routebookId); } }}>撤销</button>
          <button className="finalize-button" disabled={busy || isHistorical || !routebook?.current_version_id} onClick={finalize}>生成最终页</button>
        </div>
      </header>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => refresh(routebookId)}>重试</button></div>}
      {isHistorical && <div className="history-banner"><strong>只读历史版本 v{displayedVersion.version_number}</strong><span>{displayedVersion.change_summary}</span><button onClick={() => setDisplayedVersion(null)}>返回当前版本</button></div>}
      {preview && <div className="proposal-banner"><strong>你正在查看提案预览</strong><span>正式版本仍为 v{routebook?.current_version?.version_number}</span><button onClick={() => decide(preview, "reject")}>拒绝</button><button className="primary" onClick={() => decide(preview, "accept")}>确认修改</button></div>}

      <div className="three-columns">
        <aside className="conversation-panel" aria-label="规划对话">
          <div className="panel-heading"><p className="kicker">01 / CONVERSATION</p><h2>一起把路走顺</h2></div>
          <div className="messages" aria-live="polite">
            {!messages.length && <p className="empty-copy">还没有对话记录。把你的旅行想法发给我。</p>}
            {messages.map((message) => <article key={message.id} className={`message ${message.role}`}><small>{message.role === "user" ? "你" : "路书助手"}</small><p>{messageText(message)}</p></article>)}
          </div>
          <form className="composer" onSubmit={send}>
            <label htmlFor="message">{officialSnapshot?.days_plan.length ? `修改第 ${activeDay} 天` : "补充需求"}</label>
            <textarea id="message" disabled={isHistorical} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={isHistorical ? "历史版本为只读状态" : officialSnapshot?.days_plan.length ? "例如：下午慢一点，留出喝咖啡的时间" : "补充出发地、日期、交通方式或偏好"} />
            <button disabled={busy || isHistorical || !draft.trim()} aria-label="发送消息">↗</button>
          </form>
        </aside>

        <section className="itinerary-panel" aria-label="分日行程">
          <div className="requirement-strip">
            <span><small>目的地</small>{requirementText(snapshot, "destination")}</span>
            <span><small>天数</small>{requirementText(snapshot, "days")} 天</span>
            <span><small>节奏</small>{requirementText(snapshot, "intensity")}</span>
            <span><small>主题</small>{requirementText(snapshot, "themes")}</span>
          </div>
          <div className="day-tabs" role="tablist" aria-label="选择日期">
            {(snapshot?.days_plan ?? []).map((item) => <button role="tab" aria-selected={activeDay === item.day_number} key={item.day_number} onClick={() => setActiveDay(item.day_number)}>D{item.day_number}<small>{item.date ?? "待定"}</small></button>)}
          </div>
          <div className="day-heading"><div><p className="kicker">02 / ITINERARY</p><h2>第 {activeDay} 天</h2></div><span>{places.length} 个地点</span></div>
          {day && snapshot && <DayFacts day={day} snapshot={snapshot} />}
          {!places.length ? <div className="itinerary-empty"><i>⌁</i><h3>路线仍在展开</h3><p>确认需求后，这里会出现按天排列的地点、交通时间和天气状态。</p></div> : <ol className="stops">
            {places.map((place, index) => {
              const segment = snapshot?.route_segments.find((item) => item.origin_place_id === place.id);
              return <li key={place.id}>
                <button className={selectedPlaceId === place.id ? "selected" : ""} onClick={() => setSelectedPlaceId(place.id)}>
                  <span className="stop-index">{String(index + 1).padStart(2, "0")}</span>
                  <span><strong>{place.name}</strong><small>{place.district} · {place.semantic_type}</small></span>
                  <span className={`fact-status ${place.status}`}>{statusCopy[place.status]}</span>
                </button>
                {segment && <p className="segment">↓ {segment.distance_meters ? `${(segment.distance_meters / 1000).toFixed(1)} km` : "距离未知"} · {segment.duration_seconds ? `${Math.round(segment.duration_seconds / 60)} 分钟` : "耗时未知"} <span className={`fact-status ${segment.status}`}>{statusCopy[segment.status]}</span></p>}
              </li>;
            })}
          </ol>}
          {!snapshot?.days_plan.length && !recommendations && !isHistorical && <div className="planning-actions"><button disabled={busy || !routebook?.current_version_id} onClick={generateRecommendations}>生成地点推荐</button></div>}
          {recommendations && <section className="recommendation-list" aria-label="地点推荐候选">
            <div className="recommendation-heading"><div><p className="kicker">PLACE CANDIDATES</p><h3>先选想去的地方</h3></div><small>基于版本 {recommendations.base_version_id.slice(0, 8)}</small></div>
            {recommendations.candidates.map((candidate) => <article key={candidate.id} className={candidate.status}>
              <div><strong>{candidate.name}</strong><small>{candidate.district} · {candidate.type} · {Math.round(candidate.score * 100)} 分</small></div>
              <p>{candidate.recommendation_reason}</p>
              {!!candidate.transport_tradeoffs.length && <p className="tradeoff">取舍：{candidate.transport_tradeoffs.join("；")}</p>}
              <div className="candidate-actions">
                <span>{candidate.status === "proposed" ? "等待选择" : candidate.status}</span>
                {candidate.status === "proposed" && <><button disabled={busy} onClick={() => feedback(candidate.id, "reject", "not_interested")}>不感兴趣</button><button disabled={busy} onClick={() => feedback(candidate.id, "reject", "too_far")}>太远</button><button className="primary" disabled={busy} onClick={() => feedback(candidate.id, "accept")}>加入行程</button></>}
              </div>
            </article>)}
            <button className="build-itinerary" disabled={busy || recommendations.candidates.filter((item) => item.status === "accepted").length < 3} onClick={generateItinerary}>生成分日行程 →</button>
          </section>}
          {!!proposals.filter((item) => item.status === "pending").length && <section className="proposal-list"><h3>待确认修改</h3>{proposals.filter((item) => item.status === "pending").map((item) => <button key={item.id} onClick={() => setPreview(item)}>查看提案 · {item.risk_flags.length} 项风险</button>)}</section>}
          {!!versions.length && <details className="version-history"><summary>版本历史 · {versions.length}</summary>{versions.map((version) => <div key={version.id}><strong>v{version.version_number}</strong><span>{version.change_summary}</span><time>{new Date(version.created_at).toLocaleString("zh-CN")}</time><button disabled={version.id === routebook?.current_version_id} onClick={() => setDisplayedVersion(version)}>{version.id === routebook?.current_version_id ? "当前" : "查看"}</button></div>)}</details>}
        </section>

        <section className="map-panel" aria-label="行程地图">
          <div className="map-meta"><p className="kicker">03 / MAP</p><span>{preview ? "PROPOSED" : `VERSION ${displayedVersion?.version_number ?? routebook?.current_version?.version_number ?? "—"}`}</span></div>
          <AmapMap places={places} selectedPlaceId={selectedPlaceId} onSelect={setSelectedPlaceId} fallback={<CoordinateMap places={places} selectedPlaceId={selectedPlaceId} onSelect={setSelectedPlaceId} />} />
          <div className="map-legend"><span><i className="verified" />已验证</span><span><i className="unverified" />未验证</span><span><i className="stale" />已过期</span><span><i className="unavailable" />不可用</span><span><i className="conflicted" />有冲突</span><span><i className="proposed" />提案</span></div>
        </section>
      </div>
    </main>
  );
}

function CoordinateMap({ places, selectedPlaceId, onSelect }: { places: RouteBookSnapshot["places"]; selectedPlaceId: string | null; onSelect: (id: string) => void }) {
  if (!places.length) return <div className="map-empty"><span>31°N</span><strong>等待路线坐标</strong><small>地图与行程将绑定同一版本出现</small><span>118°E</span></div>;
  const lng = places.map((item) => item.longitude);
  const lat = places.map((item) => item.latitude);
  const minLng = Math.min(...lng), maxLng = Math.max(...lng), minLat = Math.min(...lat), maxLat = Math.max(...lat);
  const point = (value: number, min: number, max: number) => max === min ? 50 : 12 + ((value - min) / (max - min)) * 76;
  return <div className="coordinate-map">
    <svg viewBox="0 0 100 100" role="img" aria-label="当天地点坐标示意图">
      <polyline points={places.map((place) => `${point(place.longitude, minLng, maxLng)},${100 - point(place.latitude, minLat, maxLat)}`).join(" ")} />
    </svg>
    {places.map((place, index) => <button key={place.id} className={`map-marker ${place.status} ${selectedPlaceId === place.id ? "selected" : ""}`} style={{ left: `${point(place.longitude, minLng, maxLng)}%`, top: `${100 - point(place.latitude, minLat, maxLat)}%` }} onClick={() => onSelect(place.id)} aria-label={`定位到 ${place.name}`}>{index + 1}<span>{place.name}</span></button>)}
  </div>;
}

function DayFacts({ day, snapshot }: {
  day: RouteBookSnapshot["days_plan"][number];
  snapshot: RouteBookSnapshot;
}) {
  const weather = snapshot.weather.filter((item) => day.weather_refs.includes(item.ref));
  const warnings = snapshot.warnings.filter((item) => {
    const warningDay = item.day_number;
    return warningDay == null || warningDay === day.day_number;
  });
  if (!weather.length && !warnings.length && !day.notes.length) return null;
  return <section className="day-facts" aria-label="天气、预警和行程备注">
    {weather.map((item) => <span key={item.ref} className={`weather-fact ${item.status}`}><small>天气</small>{typeof item.payload.textDay === "string" ? item.payload.textDay : "预报已记录"}<i>{statusCopy[item.status]}</i></span>)}
    {warnings.map((warning, index) => <span key={index} className="warning-fact"><small>预警</small>{typeof warning.title === "string" ? warning.title : "天气风险提示"}</span>)}
    {day.notes.map((note) => <span key={note} className="note-fact"><small>备注</small>{note}</span>)}
  </section>;
}
