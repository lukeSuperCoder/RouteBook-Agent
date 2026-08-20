"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  type AmbiguousPlaceCandidate,
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

type PlanningPhase =
  | "understanding"
  | "clarifying"
  | "confirming_requirements"
  | "selecting_places"
  | "building_itinerary"
  | "itinerary_ready"
  | "editing"
  | "failed";

type OptimisticMessage = {
  id: string;
  text: string;
  status: "sending" | "failed";
};

type PlaceConfirmation = {
  dayNumber: number;
  originalText: string;
  candidates: AmbiguousPlaceCandidate[];
};

function ambiguousCandidates(value: unknown): AmbiguousPlaceCandidate[] {
  if (!Array.isArray(value)) return [];
  return value.filter((candidate): candidate is AmbiguousPlaceCandidate => {
    if (!candidate || typeof candidate !== "object") return false;
    const item = candidate as Record<string, unknown>;
    const coordinate = item.coordinate as Record<string, unknown> | undefined;
    return typeof item.provider_place_id === "string"
      && typeof item.name === "string"
      && typeof coordinate?.longitude === "number"
      && typeof coordinate?.latitude === "number";
  });
}

type ClarificationQuestion = {
  question_id: string;
  issue_code: string;
  fields: string[];
  prompt: string;
  input_type?: "single_choice" | "multi_choice" | "date" | "text";
  required?: boolean;
  options?: Array<{
    value: string;
    label: string;
    description?: string | null;
    recommended?: boolean;
    recommendation_reason?: string | null;
  }>;
  allow_skip?: boolean;
  skip_label?: string | null;
  priority?: number;
  information_gain?: number;
  rationale?: string | null;
  recommended_option_value?: string | null;
};

const phaseCopy: Record<PlanningPhase, { eyebrow: string; title: string; description: string }> = {
  understanding: { eyebrow: "UNDERSTANDING", title: "正在理解你的旅行", description: "我会先整理已经明确的条件，再找出真正影响路线的问题。" },
  clarifying: { eyebrow: "CLARIFYING", title: "先确认几个关键条件", description: "补齐这些信息后，地点推荐和路线安排会更可靠。" },
  confirming_requirements: { eyebrow: "REQUIREMENTS", title: "核对旅行需求", description: "当前条件已经足够开始推荐，你仍可以继续补充偏好。" },
  selecting_places: { eyebrow: "PLACE SELECTION", title: "选择想去的地方", description: "接受、排除或替换候选，系统会根据你的反馈继续收敛。" },
  building_itinerary: { eyebrow: "ROUTE BUILDING", title: "正在编排行程", description: "系统正在组合日期、地点和交通约束，并检查路线是否走得通。" },
  itinerary_ready: { eyebrow: "ITINERARY READY", title: "完整路线已生成", description: "逐日确认安排；准备好后进入完整版路书查看全程地图与汇总路线。" },
  editing: { eyebrow: "PROPOSAL", title: "检查本次调整", description: "当前展示的是修改预览，确认后才会写入正式版本。" },
  failed: { eyebrow: "NEEDS ATTENTION", title: "这一步没有完成", description: "保留现有内容，你可以重试或换一种说法继续。" },
};

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

type Requirements = RouteBookSnapshot["requirements"];

function requirementText(requirements: Requirements | undefined, key: string): string {
  const value = requirements?.[key]?.value;
  if (Array.isArray(value)) return value.length ? value.join("、") : "未设置";
  if (typeof value === "boolean") return value ? "接受" : "不接受";
  return value == null || value === "" ? "未设置" : String(value);
}

function requirementStatus(requirements: Requirements | undefined, key: string): { label: string; className: string } {
  const field = requirements?.[key];
  const status = field?.decision_status ?? (field?.confirmed ? "confirmed" : field?.value == null ? "missing" : "suggested");
  const labels = { missing: "待补充", suggested: "系统建议", confirmed: "已确认", skipped: "已跳过", conflicted: "有冲突" };
  return { label: labels[status], className: status };
}

function clarificationRequirements(messages: ConversationMessage[]): Requirements | null {
  const latest = [...messages].reverse().find((message) =>
    message.role === "assistant" && message.kind === "requirement_clarification",
  );
  const requirements = latest?.payload.requirements;
  return requirements && typeof requirements === "object" && !Array.isArray(requirements)
    ? requirements as Requirements
    : null;
}

function clarificationQuestions(messages: ConversationMessage[]): ClarificationQuestion[] {
  const latest = [...messages].reverse().find((message) =>
    message.role === "assistant" && message.kind === "requirement_clarification",
  );
  if (!latest || !Array.isArray(latest.payload.questions)) return [];
  return latest.payload.questions.filter((question): question is ClarificationQuestion => {
    if (typeof question !== "object" || question === null) return false;
    const candidate = question as Partial<ClarificationQuestion>;
    return typeof candidate.question_id === "string" && typeof candidate.prompt === "string";
  });
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
  const [activeDay, setActiveDay] = useState(1);
  const [draft, setDraft] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [streamGeneration, setStreamGeneration] = useState(0);
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [placeConfirmation, setPlaceConfirmation] = useState<PlaceConfirmation | null>(null);
  const [optimisticMessages, setOptimisticMessages] = useState<OptimisticMessage[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const autoRecommendationVersionRef = useRef<string | null>(null);

  const refresh = useCallback(async (id: string) => {
    const [book, thread, changes, history, latestRecommendations, activeRuns] = await Promise.all([
      routeBookApi.get(id),
      routeBookApi.messages(id),
      routeBookApi.proposals(id),
      routeBookApi.versions(id),
      routeBookApi.recommendations(id).catch(() => null),
      routeBookApi.activeWorkflows(id).catch(() => []),
    ]);
    setRoutebook(book);
    setMessages(thread);
    setProposals(changes);
    setVersions(history);
    setRecommendations(book.current_version?.snapshot.days_plan.length ? null : latestRecommendations);
    setDisplayedVersion((current) => history.find((item) => item.id === current?.id) ?? null);
    setPreview((current) => changes.find((item) => item.id === current?.id) ?? null);
    const activeRun = activeRuns[0];
    if (activeRun && activeRun.status !== "interrupted") {
      setRunId(activeRun.id);
      setProgress({
        stage: activeRun.current_stage,
        status: activeRun.status,
        message: activeRun.message ?? "正在恢复规划任务",
        progress: { completed: 0, total: 1 },
      });
    }
    const latestMessage = thread.at(-1);
    const latestClarification = latestMessage?.role === "assistant"
      && latestMessage.kind === "requirement_clarification"
      ? latestMessage
      : null;
    if ((!activeRun || activeRun.status === "interrupted") && latestClarification) {
      setRunId(latestClarification.workflow_run_id);
      setProgress({
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
      if (next.status === "failed") setError(next.message || "规划任务执行失败，请重试");
      if (["completed", "failed", "interrupted"].includes(next.status)) {
        source.close();
        refresh(routebookId).catch(() => undefined);
      }
    };
    source.addEventListener("snapshot", receive as EventListener);
    source.addEventListener("progress", receive as EventListener);
    source.onerror = () => setProgress((current) => current ?? {
      stage: "reconnecting",
      status: "running",
      message: "正在重新连接…",
      progress: { completed: 0, total: 1 },
    });
    return () => source.close();
  }, [refresh, routebookId, runId, streamGeneration]);

  useEffect(() => {
    if (progress?.status !== "running" || progress.stage !== "extracting_requirements") return;
    const timer = window.setTimeout(() => {
      setProgress((current) => current?.status === "running" && current.stage === "extracting_requirements"
        ? { ...current, message: "正在校验需求理解结果，可能还需要几秒…" }
        : current);
    }, 8000);
    return () => window.clearTimeout(timer);
  }, [progress?.stage, progress?.status]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [messages, optimisticMessages, progress?.message]);

  const officialSnapshot = displayedVersion?.snapshot ?? routebook?.current_version?.snapshot ?? null;
  const snapshot = preview?.preview_snapshot ?? officialSnapshot;
  const pendingRequirements = useMemo(() => clarificationRequirements(messages), [messages]);
  const displayedRequirements = pendingRequirements ?? snapshot?.requirements;
  const isHistorical = displayedVersion !== null;
  const day = snapshot?.days_plan.find((item) => item.day_number === activeDay);
  const places = useMemo(() => {
    const ids = new Set(day?.place_ids ?? []);
    return snapshot?.places.filter((place) => ids.has(place.id)) ?? [];
  }, [day, snapshot]);
  const phase = useMemo<PlanningPhase>(() => {
    if (error || progress?.status === "failed") return "failed";
    if (preview) return "editing";
    if (snapshot?.days_plan.length) return "itinerary_ready";
    if (recommendations) return "selecting_places";
    if (progress?.stage === "generating_recommendations" || progress?.message.includes("地点推荐") || progress?.message.includes("推荐任务")) return "selecting_places";
    if (progress?.stage === "waiting_for_clarification" || progress?.status === "interrupted") return "clarifying";
    if (busy || progress?.status === "running" || progress?.status === "queued") return "understanding";
    return routebook?.current_version_id ? "confirming_requirements" : "understanding";
  }, [busy, error, preview, progress, recommendations, routebook?.current_version_id, snapshot?.days_plan.length]);
  const phaseContent = phaseCopy[phase];
  const isActive = busy || progress?.status === "running" || progress?.status === "queued";
  const activeQuestions = useMemo(() => clarificationQuestions(messages), [messages]);
  const requirementsJustConfirmed = useMemo(() => {
    const latest = messages.at(-1);
    return latest?.role === "assistant"
      && latest.kind === "status"
      && messageText(latest).startsWith("需求已确认");
  }, [messages]);

  useEffect(() => {
    const versionId = routebook?.current_version_id;
    if (
      phase !== "confirming_requirements"
      || !routebookId
      || !versionId
      || isHistorical
      || busy
      || !requirementsJustConfirmed
      || autoRecommendationVersionRef.current === versionId
    ) return;
    autoRecommendationVersionRef.current = versionId;
    setBusy(true);
    setError(null);
    routeBookApi.generateRecommendations(routebookId)
      .then((accepted) => {
        setRunId(accepted.workflow_run_id);
        setProgress({
          stage: "generating_recommendations",
          status: "queued",
          message: "需求已确认，正在生成地点推荐…",
          progress: { completed: 0, total: 1 },
        });
      })
      .catch((reason: unknown) => {
        autoRecommendationVersionRef.current = null;
        setError(reason instanceof Error ? reason.message : "推荐生成失败");
      })
      .finally(() => setBusy(false));
  }, [busy, isHistorical, phase, requirementsJustConfirmed, routebook?.current_version_id, routebookId]);

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

  async function submitMessage(rawText: string, selectedPlace?: AmbiguousPlaceCandidate) {
    if (!routebookId || !rawText.trim() || busy) return;
    const text = rawText.trim();
    const optimisticId = `optimistic-${crypto.randomUUID()}`;
    setOptimisticMessages((current) => [...current, { id: optimisticId, text, status: "sending" }]);
    setDraft("");
    setBusy(true);
    setError(null);
    if (selectedPlace) setPlaceConfirmation(null);
    try {
      if (officialSnapshot?.days_plan.length) {
        setProgress({
          stage: "validating",
          status: "running",
          message: `正在理解并检查第 ${activeDay} 天的修改…`,
          progress: { completed: 0, total: 1 },
        });
        const result = await routeBookApi.editDay(routebookId, activeDay, text, selectedPlace);
        if (result.proposal) {
          setPreview(result.proposal);
          setProgress({
            stage: "waiting_for_change_confirmation",
            status: "completed",
            message: "修改方案已生成，请预览并确认",
            progress: { completed: 1, total: 1 },
          });
        } else if (result.status === "needs_clarification") {
          throw new Error(result.clarification ?? "还需要补充修改范围");
        } else {
          setProgress({
            stage: "completed",
            status: "completed",
            message: `第 ${activeDay} 天已更新`,
            progress: { completed: 1, total: 1 },
          });
        }
      } else {
        const isResume = Boolean(runId && progress?.status === "interrupted");
        if (isResume) {
          setProgress({
            stage: "extracting_requirements",
            status: "running",
            message: "已收到回答，正在继续确认需求…",
            progress: { completed: 0, total: 1 },
          });
        }
        const accepted = isResume
          ? await routeBookApi.resume(runId!, text)
          : await routeBookApi.sendMessage(routebookId, text);
        setRunId(accepted.workflow_run_id);
        if (isResume) setStreamGeneration((current) => current + 1);
      }
      await refresh(routebookId);
      setOptimisticMessages((current) => current.filter((message) => message.id !== optimisticId));
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "PLACE_AMBIGUOUS") {
        const candidates = ambiguousCandidates(reason.details.candidates);
        if (candidates.length) {
          setOptimisticMessages((current) => current.filter((message) => message.id !== optimisticId));
          setPlaceConfirmation({ dayNumber: activeDay, originalText: text, candidates });
          setProgress({
            stage: "waiting_for_place_confirmation",
            status: "interrupted",
            message: "找到多个同名地点，请确认具体地点",
            progress: { completed: 0, total: 1 },
          });
          return;
        }
      }
      setOptimisticMessages((current) => current.map((message) =>
        message.id === optimisticId ? { ...message, status: "failed" } : message,
      ));
      setError(reason instanceof Error ? reason.message : "消息发送失败");
    } finally {
      setBusy(false);
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitMessage(draft);
  }

  function restoreFailedMessage(message: OptimisticMessage) {
    setDraft(message.text);
    setOptimisticMessages((current) => current.filter((item) => item.id !== message.id));
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
    const storageKey = `routebook-final:${routebookId}:${routebook.current_version_id}`;
    const cachedUrl = window.sessionStorage.getItem(storageKey);
    if (cachedUrl) {
      window.location.assign(cachedUrl);
      return;
    }
    setBusy(true);
    try {
      const result = await routeBookApi.finalize(routebookId, routebook.current_version_id);
      window.sessionStorage.setItem(storageKey, result.share_url);
      window.location.assign(result.share_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "最终页面生成失败");
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
      const accepted = await routeBookApi.generateItinerary(routebookId);
      setRunId(accepted.workflow_run_id);
      setProgress({
        stage: "queued",
        status: "queued",
        message: "行程任务已进入队列",
        progress: { completed: 0, total: 3 },
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "行程生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function cancelActiveRun() {
    if (!runId) return;
    try {
      await routeBookApi.cancelWorkflow(runId);
      setProgress((current) => current ? { ...current, status: "cancelled", message: "任务已取消" } : null);
      setRunId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "取消任务失败");
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
            <textarea id="trip-brief" name="tripBrief" rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="例如：9 月从上海自驾去南京三天，想看建筑和梧桐，必去中山陵，不想赶早。" />
            <button type="submit" disabled={busy || !draft.trim()}>{busy ? "正在建立坐标…" : "开始规划 →"}</button>
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
          <span className={`phase-badge phase-${phase}`}>{phaseContent.title}</span>
          {isHistorical && <button onClick={() => setDisplayedVersion(null)}>返回当前版本</button>}
          <button disabled={busy || isHistorical || !routebook?.current_version?.parent_version_id} onClick={async () => { if (routebookId) { await routeBookApi.undo(routebookId); await refresh(routebookId); } }}>撤销</button>
        </div>
      </header>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => refresh(routebookId)}>重试</button></div>}
      {isHistorical && <div className="history-banner"><strong>只读历史版本 v{displayedVersion.version_number}</strong><span>{displayedVersion.change_summary}</span><button onClick={() => setDisplayedVersion(null)}>返回当前版本</button></div>}
      {preview && <div className="proposal-banner"><strong>你正在查看提案预览</strong><span>正式版本仍为 v{routebook?.current_version?.version_number}</span><button onClick={() => decide(preview, "reject")}>拒绝</button><button className="primary" onClick={() => decide(preview, "accept")}>确认修改</button></div>}

      <div className="workspace-columns">
        <aside className="conversation-panel" aria-label="规划对话">
          <div className="panel-heading"><p className="kicker">01 / CONVERSATION</p><h2>一起把路走顺</h2></div>
          <div className="messages">
            {!messages.length && <p className="empty-copy">还没有对话记录。把你的旅行想法发给我。</p>}
            {messages.map((message) => <article key={message.id} className={`message ${message.role}`}><small>{message.role === "user" ? "你" : "路书助手"}</small><p>{messageText(message)}</p></article>)}
            {phase === "clarifying" && activeQuestions[0]?.options?.length ? <div className="inline-quick-replies" aria-label="快捷回答">
              {activeQuestions[0].options.map((option) => <button type="button" key={option.value} disabled={busy} onClick={() => submitMessage(option.value)}>{option.label}</button>)}
            </div> : null}
            {optimisticMessages.map((message) => <article key={message.id} className={`message user optimistic ${message.status}`}>
              <small>{message.status === "sending" ? "你 · 正在发送" : "你 · 发送失败"}</small>
              <p>{message.text}</p>
              {message.status === "failed" && <button type="button" onClick={() => restoreFailedMessage(message)}>放回输入框</button>}
            </article>)}
            {placeConfirmation && <fieldset className="place-confirmation">
              <legend>请选择要加入第 {placeConfirmation.dayNumber} 天的具体地点</legend>
              <p>“{placeConfirmation.originalText}”匹配到多个地点，请根据名称和地址确认：</p>
              {placeConfirmation.candidates.map((candidate) => <button
                type="button"
                key={candidate.provider_place_id}
                disabled={busy}
                onClick={() => submitMessage(placeConfirmation.originalText, candidate)}
              >
                <strong>{candidate.name}</strong>
                <small>{[candidate.district, candidate.address].filter(Boolean).join(" · ") || "暂无详细地址"}</small>
                <span>选择此地点 →</span>
              </button>)}
              <button type="button" className="cancel-place-confirmation" onClick={() => {
                setDraft(placeConfirmation.originalText);
                setPlaceConfirmation(null);
                setProgress(null);
              }}>都不是，修改输入</button>
            </fieldset>}
            {isActive && <article className="assistant-activity" aria-label="路书助手正在工作">
              <span className="activity-mark" aria-hidden="true"><i /><i /><i /></span>
              <span><strong>{progress?.message ?? "正在接收并整理你的旅行想法"}</strong><small>完成后会自动更新右侧任务面板</small></span>
            </article>}
            <div ref={messagesEndRef} />
          </div>
          <form className="composer" onSubmit={send}>
            <label htmlFor="message">{officialSnapshot?.days_plan.length ? `修改第 ${activeDay} 天` : "补充需求"}</label>
            <textarea id="message" disabled={isHistorical} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={isHistorical ? "历史版本为只读状态" : officialSnapshot?.days_plan.length ? "例如：下午慢一点，留出喝咖啡的时间" : "补充出发地、日期、交通方式或偏好"} />
            <button disabled={busy || isHistorical || !draft.trim()} aria-label="发送消息">↗</button>
          </form>
        </aside>

        <section className={`itinerary-panel planning-phase-${phase}`} aria-labelledby="planning-panel-title">
          <header className="planning-stage-header">
            <div><p className="kicker">02 / {phaseContent.eyebrow}</p><h2 id="planning-panel-title">{phaseContent.title}</h2><p>{phaseContent.description}</p></div>
            <div className="stage-actions">
              <span className={`stage-state ${isActive ? "active" : ""}`}><i aria-hidden="true" />{isActive ? "任务进行中" : phase === "itinerary_ready" ? "路线已就绪" : "等待你的操作"}</span>
              {phase === "itinerary_ready" && !isHistorical && <button type="button" className="view-full-route" disabled={busy} onClick={finalize}>{busy ? "正在生成完整版…" : routebook?.latest_final_version_id === routebook?.current_version_id ? "返回完整版路书 →" : "查看完整版路书 →"}</button>}
              {isActive && runId && <button type="button" className="cancel-workflow" onClick={cancelActiveRun}>取消当前任务</button>}
            </div>
          </header>
          <div className="requirement-strip">
            {[{ key: "destination", label: "目的地" }, { key: "days", label: "天数", suffix: " 天" }, { key: "intensity", label: "节奏" }, { key: "themes", label: "主题" }].map((item) => {
              const state = requirementStatus(displayedRequirements, item.key);
              const reason = displayedRequirements?.[item.key]?.suggestion_reason;
              return <span key={item.key} title={reason ?? undefined}><small>{item.label}<i className={`requirement-state ${state.className}`}>{state.label}</i></small>{requirementText(displayedRequirements, item.key)}{item.suffix}{reason && <em className="suggestion-reason">{reason}</em>}</span>;
            })}
          </div>
          {phase === "clarifying" && activeQuestions.length > 0 && <RequirementConfirmation questions={activeQuestions} busy={busy} onAnswer={submitMessage} />}
          <nav className="day-tabs" aria-label="选择日期">
            {(snapshot?.days_plan ?? []).map((item) => <button aria-pressed={activeDay === item.day_number} key={item.day_number} onClick={() => setActiveDay(item.day_number)}>D{item.day_number}<small>{item.date ?? "待定"}</small></button>)}
          </nav>
          {!!snapshot?.days_plan.length && <div className="day-heading"><div><p className="kicker">DAY PLAN</p><h2>第 {activeDay} 天</h2></div><div className="day-heading-actions"><span>{places.length} 个地点</span></div></div>}
          {day && snapshot && <DayFacts day={day} snapshot={snapshot} />}
          {!places.length && !recommendations ? <PlanningEmptyState phase={phase} progress={progress} /> : places.length ? <ol className="stops">
            {places.map((place, index) => {
              const segment = snapshot?.route_segments.find((item) => item.origin_place_id === place.id);
              return <li key={place.id}>
                <div className="stop-row">
                  <span className="stop-index">{String(index + 1).padStart(2, "0")}</span>
                  <span><strong>{place.name}</strong><small>{place.district} · {place.semantic_type}</small></span>
                  <span className={`fact-status ${place.status}`}>{statusCopy[place.status]}</span>
                </div>
                {segment && <p className="segment">↓ {segment.distance_meters ? `${(segment.distance_meters / 1000).toFixed(1)} km` : "距离未知"} · {segment.duration_seconds ? `${Math.round(segment.duration_seconds / 60)} 分钟` : "耗时未知"} <span className={`fact-status ${segment.status}`}>{statusCopy[segment.status]}</span></p>}
              </li>;
            })}
          </ol> : null}
          {recommendations && !snapshot?.days_plan.length && <section className="recommendation-list" aria-label="地点推荐候选">
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
            {(() => {
              const acceptedCount = recommendations.candidates.filter((item) => item.status === "accepted").length;
              const missingCount = Math.max(0, 3 - acceptedCount);
              return <div className="build-itinerary-bar">
                <span aria-live="polite">已选 {acceptedCount} 个{missingCount ? ` · 再选 ${missingCount} 个即可生成` : " · 可以开始编排行程"}</span>
                <button className="build-itinerary" disabled={busy || missingCount > 0} onClick={generateItinerary}>{missingCount ? `还需选择 ${missingCount} 个地点` : "生成分日行程 →"}</button>
              </div>;
            })()}
          </section>}
          {!!proposals.filter((item) => item.status === "pending").length && <section className="proposal-list"><h3>待确认修改</h3>{proposals.filter((item) => item.status === "pending").map((item) => <button key={item.id} onClick={() => setPreview(item)}>查看提案 · {item.risk_flags.length} 项风险</button>)}</section>}
          {!!versions.length && <details className="version-history"><summary>版本历史 · {versions.length}</summary>{versions.map((version) => <div key={version.id}><strong>v{version.version_number}</strong><span>{version.change_summary}</span><time>{new Date(version.created_at).toLocaleString("zh-CN")}</time><button disabled={version.id === routebook?.current_version_id} onClick={() => setDisplayedVersion(version)}>{version.id === routebook?.current_version_id ? "当前" : "查看"}</button></div>)}</details>}
        </section>

      </div>
      <p className="workflow-announcer visually-hidden" aria-live="polite" aria-atomic="true">{progress?.message ?? phaseContent.title}</p>
    </main>
  );
}

function PlanningEmptyState({ phase, progress }: { phase: PlanningPhase; progress: ProgressEvent | null }) {
  const content = phaseCopy[phase];
  return <div className={`planning-empty phase-${phase}`}>
    <div className="planning-orbit" aria-hidden="true"><span /><i /></div>
    <p className="kicker">CURRENT TASK</p>
    <h3>{content.title}</h3>
    <p>{progress?.message ?? content.description}</p>
    <ol aria-label="规划步骤">
      <li className={phase === "understanding" ? "current" : "done"}><span>01</span>理解旅行想法</li>
      <li className={phase === "clarifying" || phase === "confirming_requirements" ? "current" : ""}><span>02</span>确认关键需求</li>
      <li className={phase === "selecting_places" ? "current" : ""}><span>03</span>选择推荐地点</li>
      <li><span>04</span>生成完整路线</li>
    </ol>
  </div>;
}

function RequirementConfirmation({ questions, busy, onAnswer }: {
  questions: ClarificationQuestion[];
  busy: boolean;
  onAnswer: (text: string) => Promise<void>;
}) {
  return <section className="requirement-confirmation" aria-labelledby="requirement-confirmation-title">
    <header><p className="kicker">NEEDS YOUR INPUT</p><h3 id="requirement-confirmation-title">把关键条件确认清楚</h3><span>{questions.length} 项待确认</span></header>
    <ol role="list">
      {questions.map((question, index) => <li key={question.question_id}>
        <div className="question-index">{String(index + 1).padStart(2, "0")}</div>
        <div className="question-body">
          <h4>{question.prompt}</h4>
          {question.rationale && <p className="question-rationale">为什么现在问：{question.rationale}</p>}
          {question.options?.length ? <div className="choice-grid">
            {question.options.map((option) => <button type="button" className={option.recommended ? "recommended" : ""} key={option.value} disabled={busy} onClick={() => onAnswer(option.value)}>
              <strong>{option.label}{option.recommended && <span>建议</span>}</strong>{option.description && <small>{option.description}</small>}{option.recommendation_reason && <small className="recommendation-reason">{option.recommendation_reason}</small>}
            </button>)}
          </div> : question.input_type === "date" ? <div className="date-answer">
            <label htmlFor={`answer-${question.question_id}`}>选择日期</label>
            <input id={`answer-${question.question_id}`} type="date" disabled={busy} onChange={(event) => { if (event.target.value) onAnswer(`行程从 ${event.target.value} 开始`); }} />
            {question.allow_skip && <button type="button" disabled={busy} onClick={() => onAnswer(question.skip_label ?? "日期暂未确定")}>{question.skip_label ?? "暂不确定"}</button>}
          </div> : <p className="question-hint">请在左侧对话框中补充这项信息。</p>}
        </div>
      </li>)}
    </ol>
  </section>;
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
