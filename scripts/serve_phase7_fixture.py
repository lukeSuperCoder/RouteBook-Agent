"""Deterministic, stateful Phase 7 browser fixture. Never used by production services."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="RouteBook Phase 7 E2E Fixture")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROUTEBOOK_ID = "10000000-0000-4000-8000-000000000001"
RUN_ID = "20000000-0000-4000-8000-000000000001"
BASE_VERSION_ID = "30000000-0000-4000-8000-000000000001"
PLACE_IDS = [f"40000000-0000-4000-8000-00000000000{i}" for i in range(1, 5)]
now = datetime.now(UTC).isoformat()


def requirement(value: object = None, *, confirmed: bool = False) -> dict[str, object]:
    return {
        "value": value,
        "source": "explicit" if value is not None else "missing",
        "confidence": 1 if value is not None else 0,
        "confirmed": confirmed,
    }


def empty_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "requirements": {
            "origin": requirement(),
            "destination": requirement("南京", confirmed=True),
            "start_date": requirement(),
            "days": requirement(3, confirmed=True),
            "transport_mode": requirement(),
            "companions": requirement([]),
            "themes": requirement(["建筑", "梧桐"], confirmed=True),
            "intensity": requirement("relaxed", confirmed=True),
            "crowd_tolerance": requirement(),
            "suburban_acceptance": requirement(),
            "must_visit_place_ids": requirement([]),
            "optional_place_ids": requirement([]),
            "excluded_place_ids": requirement([]),
            "visited_place_ids": requirement([]),
            "must_visit_place_texts": requirement(["中山陵"], confirmed=True),
            "optional_place_texts": requirement([]),
            "excluded_place_texts": requirement([]),
            "visited_place_texts": requirement([]),
            "notes": requirement([]),
        },
        "places": [],
        "days_plan": [],
        "route_segments": [],
        "weather": [],
        "notes": [],
        "warnings": [],
    }


state: dict[str, object] = {
    "title": "从上海去南京三天，必去中山陵",
    "status": "planning",
    "version_number": 1,
    "version_id": BASE_VERSION_ID,
    "parent_version_id": None,
    "snapshot": empty_snapshot(),
    "messages": [],
    "interrupted": False,
    "recommendations": None,
    "versions": [],
    "share_token": None,
}


def version() -> dict[str, object]:
    return {
        "id": state["version_id"],
        "routebook_id": ROUTEBOOK_ID,
        "version_number": state["version_number"],
        "parent_version_id": state["parent_version_id"],
        "snapshot": deepcopy(state["snapshot"]),
        "change_type": "edit" if state["version_number"] != 1 else "create",
        "change_summary": "确定性阶段七浏览器回放",
        "source_user_message": None,
        "workflow_run_id": RUN_ID,
        "created_at": now,
    }


def add_message(role: str, kind: str, payload: dict[str, object]) -> dict[str, object]:
    message: dict[str, object] = {
        "id": str(uuid4()),
        "routebook_id": ROUTEBOOK_ID,
        "workflow_run_id": RUN_ID,
        "message_id": f"fixture-{uuid4()}",
        "role": role,
        "kind": kind,
        "payload": payload,
        "created_at": datetime.now(UTC).isoformat(),
    }
    messages = state["messages"]
    assert isinstance(messages, list)
    messages.append(message)
    return message


@app.post("/api/routebooks")
def create_routebook() -> dict[str, object]:
    return {"routebook_id": ROUTEBOOK_ID, "workflow_run_id": RUN_ID}


@app.get("/api/routebooks/{routebook_id}")
def get_routebook(routebook_id: str) -> dict[str, object]:
    assert routebook_id == ROUTEBOOK_ID
    return {
        "id": ROUTEBOOK_ID,
        "title": state["title"],
        "status": state["status"],
        "current_version_id": state["version_id"],
        "latest_final_version_id": state["version_id"] if state["status"] == "final" else None,
        "current_version": version(),
        "created_at": now,
        "updated_at": now,
    }


@app.post("/api/routebooks/{routebook_id}/messages")
async def send_message(routebook_id: str, request: Request) -> dict[str, object]:
    payload = await request.json()
    message = add_message("user", "requirement_input", {"text": payload["text"]})
    state["interrupted"] = True
    add_message(
        "assistant",
        "requirement_clarification",
        {
            "questions": [
                {"prompt": "请补充从哪里出发？"},
                {"prompt": "计划哪一天出发？"},
                {"prompt": "主要使用哪种交通方式？"},
            ]
        },
    )
    return {
        "message": message,
        "workflow_run_id": RUN_ID,
        "workflow_status": "interrupted",
        "reused": False,
        "status_url": f"/api/workflow-runs/{RUN_ID}",
        "events_url": f"/api/workflow-runs/{RUN_ID}/events",
    }


@app.post("/api/workflow-runs/{run_id}/resume")
async def resume(run_id: str, request: Request) -> dict[str, object]:
    assert run_id == RUN_ID
    payload = await request.json()
    message = add_message("user", "requirement_clarification", {"text": payload["text"]})
    snapshot = state["snapshot"]
    assert isinstance(snapshot, dict)
    requirements = snapshot["requirements"]
    assert isinstance(requirements, dict)
    requirements.update(
        origin=requirement("上海", confirmed=True),
        start_date=requirement("2026-09-01", confirmed=True),
        transport_mode=requirement("driving", confirmed=True),
    )
    state["interrupted"] = False
    state["status"] = "editable"
    return {
        "message": message,
        "workflow_run_id": RUN_ID,
        "workflow_status": "completed",
        "reused": False,
        "status_url": f"/api/workflow-runs/{RUN_ID}",
        "events_url": f"/api/workflow-runs/{RUN_ID}/events",
    }


@app.get("/api/workflow-runs/{run_id}/events")
def events(run_id: str) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        await asyncio.sleep(0.1)
        status = "interrupted" if state["interrupted"] else "completed"
        event = json.dumps(
            {
                "stage": "waiting_for_clarification",
                "status": status,
                "message": "等待你补充信息" if status == "interrupted" else "需求已确认",
                "progress": {"completed": 1, "total": 1},
            },
            ensure_ascii=False,
        )
        yield f"event: progress\ndata: {event}\n\n"

    assert run_id == RUN_ID
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/routebooks/{routebook_id}/messages")
def messages(routebook_id: str) -> object:
    assert routebook_id == ROUTEBOOK_ID
    return state["messages"]


@app.get("/api/routebooks/{routebook_id}/proposals")
def proposals(routebook_id: str) -> list[object]:
    assert routebook_id == ROUTEBOOK_ID
    return []


@app.get("/api/routebooks/{routebook_id}/versions")
def versions(routebook_id: str) -> list[object]:
    assert routebook_id == ROUTEBOOK_ID
    history = state["versions"]
    assert isinstance(history, list)
    return [version(), *history]


def candidates() -> list[dict[str, object]]:
    names = ["中山陵", "明孝陵", "南京博物院", "颐和路"]
    current = state["recommendations"]
    statuses = current if isinstance(current, dict) else {}
    return [
        {
            "id": PLACE_IDS[index],
            "provider_place_id": f"amap-{index}",
            "name": name,
            "type": "attraction",
            "address": "脱敏地址",
            "district": "玄武区" if index < 3 else "鼓楼区",
            "recommendation_reason": "符合建筑与梧桐主题，且能与当天路线自然衔接。",
            "transport_tradeoffs": ["高峰时段可能拥堵"],
            "score": 0.95 - index * 0.03,
            "score_evidence": ["主题匹配", "路线连续"],
            "status": statuses.get(PLACE_IDS[index], "proposed"),
        }
        for index, name in enumerate(names)
    ]


def batch() -> dict[str, object]:
    return {
        "id": "50000000-0000-4000-8000-000000000001",
        "routebook_id": ROUTEBOOK_ID,
        "base_version_id": state["version_id"],
        "strategy": {},
        "metrics": {},
        "candidates": candidates(),
        "created_at": now,
    }


@app.post("/api/routebooks/{routebook_id}/recommendations")
def recommendations(routebook_id: str) -> dict[str, object]:
    assert routebook_id == ROUTEBOOK_ID
    state["recommendations"] = {}
    return batch()


@app.post("/api/routebooks/{routebook_id}/recommendations/{proposal_id}/feedback")
async def candidate_feedback(
    routebook_id: str, proposal_id: str, request: Request
) -> dict[str, object]:
    assert routebook_id == ROUTEBOOK_ID
    payload = await request.json()
    statuses = state["recommendations"]
    assert isinstance(statuses, dict)
    statuses[proposal_id] = "accepted" if payload["action"] == "accept" else "rejected"
    return batch()


@app.post("/api/routebooks/{routebook_id}/itinerary")
def itinerary(routebook_id: str) -> dict[str, object]:
    assert routebook_id == ROUTEBOOK_ID
    snapshot = state["snapshot"]
    assert isinstance(snapshot, dict)
    places = [
        {
            "id": PLACE_IDS[index],
            "provider": "amap",
            "provider_place_id": f"amap-{index}",
            "name": name,
            "address": "南京市脱敏地址",
            "district": "玄武区",
            "longitude": 118.80 + index * 0.025,
            "latitude": 32.04 + index * 0.015,
            "coordinate_system": "GCJ-02",
            "category_raw": "风景名胜",
            "category_normalized": "attraction",
            "semantic_type": "attraction",
            "status": "verified",
        }
        for index, name in enumerate(["中山陵", "明孝陵", "南京博物院"])
    ]
    snapshot.update(
        places=places,
        days_plan=[
            {
                "day_number": day,
                "date": f"2026-09-0{day}",
                "place_ids": [places[day - 1]["id"]],
                "segment_ids": [],
                "weather_refs": [f"weather-{day}"],
                "notes": [],
            }
            for day in range(1, 4)
        ],
        weather=[
            {
                "ref": f"weather-{day}",
                "place_id": places[day - 1]["id"],
                "status": "verified",
                "payload": {"textDay": "晴间多云"},
            }
            for day in range(1, 4)
        ],
    )
    state["parent_version_id"] = state["version_id"]
    state["version_id"] = "30000000-0000-4000-8000-000000000002"
    state["version_number"] = 2
    state["status"] = "editable"
    return {
        "feasible": True,
        "version_id": state["version_id"],
        "repair_attempts": 0,
        "degraded": False,
        "conflicts": [],
    }


@app.post("/api/routebooks/{routebook_id}/edits")
async def edit(routebook_id: str, request: Request) -> dict[str, object]:
    assert routebook_id == ROUTEBOOK_ID
    payload = await request.json()
    snapshot = state["snapshot"]
    assert isinstance(snapshot, dict)
    days = snapshot["days_plan"]
    assert isinstance(days, list)
    days[0]["notes"] = [payload["note"]]
    state["parent_version_id"] = state["version_id"]
    state["version_id"] = "30000000-0000-4000-8000-000000000003"
    state["version_number"] = 3
    return {
        "status": "completed",
        "version_id": state["version_id"],
        "proposal": None,
        "reused": False,
        "candidates": [],
    }


@app.post("/api/routebooks/{routebook_id}/finalize")
def finalize(routebook_id: str) -> dict[str, object]:
    assert routebook_id == ROUTEBOOK_ID
    state["status"] = "final"
    state["share_token"] = "phase7FixturePublicToken123456"
    return {
        "final_page_id": str(uuid4()),
        "routebook_id": ROUTEBOOK_ID,
        "routebook_version_id": state["version_id"],
        "public_token": state["share_token"],
        "share_url": f"/share/{state['share_token']}",
        "privacy_policy": "redact_addresses",
        "created_at": now,
    }


@app.get("/share/{token}")
def share(token: str) -> dict[str, object]:
    assert token == state["share_token"]
    snapshot = deepcopy(state["snapshot"])
    assert isinstance(snapshot, dict)
    places = snapshot["places"]
    assert isinstance(places, list)
    for place in places:
        place["address"] = ""
    return {
        "title": state["title"],
        "routebook_version_id": state["version_id"],
        "version_number": state["version_number"],
        "snapshot": snapshot,
        "privacy_policy": "redact_addresses",
        "created_at": now,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
