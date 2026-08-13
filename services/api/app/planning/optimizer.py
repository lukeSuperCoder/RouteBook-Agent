from __future__ import annotations

import math

from .models import PlanningPlace


def distance_meters(left: PlanningPlace, right: PlanningPlace) -> float:
    a = left.candidate.coordinate
    b = right.candidate.coordinate
    mean_latitude = math.radians((a.latitude + b.latitude) / 2)
    dx = (a.longitude - b.longitude) * 111_320 * math.cos(mean_latitude)
    dy = (a.latitude - b.latitude) * 110_540
    return math.hypot(dx, dy)


def nearest_neighbor(places: list[PlanningPlace]) -> list[PlanningPlace]:
    if len(places) < 2:
        return places.copy()
    remaining = places[1:].copy()
    ordered = [places[0]]
    while remaining:
        next_place = min(remaining, key=lambda item: distance_meters(ordered[-1], item))
        remaining.remove(next_place)
        ordered.append(next_place)
    return ordered


def route_length(places: list[PlanningPlace]) -> float:
    return sum(
        distance_meters(left, right) for left, right in zip(places, places[1:], strict=False)
    )


def limited_two_opt(places: list[PlanningPlace], *, maximum_passes: int = 4) -> list[PlanningPlace]:
    best = places.copy()
    best_length = route_length(best)
    for _ in range(maximum_passes):
        improved = False
        for start in range(1, len(best) - 1):
            for end in range(start + 1, len(best)):
                candidate = best[:start] + list(reversed(best[start : end + 1])) + best[end + 1 :]
                candidate_length = route_length(candidate)
                if candidate_length + 1 < best_length:
                    best, best_length, improved = candidate, candidate_length, True
        if not improved:
            break
    return best
