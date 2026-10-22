"""Hotspot scoring rules - pure functions, no database, no brand, no model.

The plan is emphatic that the public hotspot pool must not depend on a product profile, a
personal login or a paid model, and that four scenario views must not be the same array
reshuffled. Everything here is therefore generic: a leaderboard position, how many distinct
origins carry the story, how fresh the observation is, and which features the text shows.

Formulas follow the plan's ``ranking.example.json``:

* general ``G = 100 * (0.7 * R + 0.3 * C) * Q``
* scenario ``S = 0.65 * G + 0.35 * (100 * F)`` where ``F`` is the feature fit, ``unknown``
  counting as 0.5 so an unlabelled story is neither promoted nor buried.

One implementation choice is flagged rather than hidden: the package gives the *weights* but
not the exact rank-decay curve for ``R``, so a linear decay over the observed board is used
and named here as a choice. It is the kind of thing that should be agreed, not assumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RULE_VERSION = "hotspots-rules-1.0.0"
TAXONOMY_VERSION = "hotspots-taxonomy-1.0.0"

CATEGORIES = ("社会", "生活", "文化", "娱乐", "体育", "科技", "财经", "职场",
              "教育", "旅行", "美食", "健康", "城市活动", "其他")

FEATURES = ("experience", "practical", "conversational", "explanatory", "visual",
            "local_activity")

UNKNOWN_FEATURE = 0.5

# The five views the plan configures: Xiaohongshu plus the four publishing surfaces, with
# personal and store Moments kept apart because they behave differently.
VIEW_WEIGHTS: dict[str, dict[str, float]] = {
    "xiaohongshu": {"experience": 0.25, "practical": 0.25, "visual": 0.2,
                    "conversational": 0.2, "explanatory": 0.1},
    "moments_personal": {"conversational": 0.45, "local_activity": 0.25,
                         "practical": 0.2, "experience": 0.1},
    "moments_store": {"conversational": 0.35, "local_activity": 0.3, "visual": 0.2,
                      "practical": 0.15},
    "article": {"explanatory": 0.6, "practical": 0.25, "conversational": 0.15},
    "poster": {"visual": 0.5, "local_activity": 0.3, "conversational": 0.2},
}

FRESH_FACTORS = {"fresh": 1.0, "aging": 0.8, "stale": 0.4, "unknown": 0.5,
                 "unavailable": 0.0}

POSITION_WEIGHT = 0.7
COVERAGE_WEIGHT = 0.3
COVERAGE_SATURATES_AFTER_EXTRA_PLATFORMS = 3
GENERAL_WEIGHT = 0.65
FIT_WEIGHT = 0.35

DIVERSITY = {"top_n": 20, "soft_source_share": 0.5, "soft_category_share": 0.4,
             "max_candidate_score_gap": 10.0, "relax_if_insufficient": True}

COMMERCIAL_CAUTION_FLAGS = ("tragedy", "privacy_risk", "unverified_accusation", "rights_risk")

TREND_TARGET_MINUTES = 30
TREND_MIN_MINUTES = 15
TREND_MAX_MINUTES = 45

# Deliberately empty. The plan ships these two values as proof that the pool is not filtered
# by brand, and a later change that breaks that should have to break this line too.
BRAND_FILTERS: list[str] = []
PRODUCT_PROFILE_REQUIRED = False


def position_score(rank: int, board_size: int) -> float:
    """``R`` in 0..1: a linear decay across the observed board.

    The package specifies the weight of this term but not its curve, so the shape is an
    explicit implementation choice. It is monotonic in rank, which is what the rest of the
    formula relies on.
    """
    if board_size <= 1:
        return 1.0
    rank = max(1, int(rank))
    return max(0.0, 1.0 - (rank - 1) / (board_size - 1))


def coverage_score(extra_platforms: int) -> float:
    """``C`` in 0..1: how many *distinct origins* carry the story, saturating quickly.

    Distinct origins, not distinct rows: two providers serving the same origin must count
    once, which is why the caller passes an origin count and not a member count.
    """
    extra = max(0, int(extra_platforms))
    return min(1.0, extra / COVERAGE_SATURATES_AFTER_EXTRA_PLATFORMS)


def freshness_state(verified_upstream_at: datetime | None, origin_updated_at: datetime | None,
                    now: datetime | None = None) -> str:
    """fresh / aging / stale / unknown, from *verified* observations only.

    An unverified observation cannot produce "fresh": a cache hit or a local timestamp is
    not evidence that the origin was reached. Without a verified time the state is
    ``unknown``, which the score then treats as a neutral 0.5 rather than as stale.
    """
    if verified_upstream_at is None:
        return "unknown"
    moment = origin_updated_at or verified_upstream_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    age = reference - moment
    if age <= timedelta(minutes=TREND_TARGET_MINUTES):
        return "fresh"
    if age <= timedelta(minutes=TREND_TARGET_MINUTES * 3):
        return "aging"
    return "stale"


def freshness_factor(state: str) -> float:
    return FRESH_FACTORS.get(str(state), FRESH_FACTORS["unknown"])


def feature_fit(features: dict, view_key: str) -> float:
    """``F`` in 0..1: weighted match between observed features and the view's profile.

    A feature the story does not show counts as ``unknown`` (0.5) rather than 0, so a story
    nobody has labelled yet is not penalised for the gap in our own annotation.
    """
    weights = VIEW_WEIGHTS.get(view_key)
    if not weights:
        return 0.0
    total = sum(weights.values()) or 1.0
    score = 0.0
    for name, weight in weights.items():
        value = features.get(name)
        score += weight * (UNKNOWN_FEATURE if value is None else max(0.0, min(1.0, float(value))))
    return score / total


def general_score(rank: int, board_size: int, distinct_origins: int, state: str) -> float:
    position = position_score(rank, board_size)
    coverage = coverage_score(max(0, distinct_origins - 1))
    return round(100.0 * (POSITION_WEIGHT * position + COVERAGE_WEIGHT * coverage)
                 * freshness_factor(state), 4)


def scenario_score(general: float, features: dict, view_key: str) -> float:
    fit = feature_fit(features, view_key)
    return round(GENERAL_WEIGHT * general + FIT_WEIGHT * 100.0 * fit, 4)


def commercial_blocked(risk_flags: list) -> bool:
    """Risk content is excluded from commercial recommendation, not merely down-ranked.

    The plan separates the *source view* (which may show it, with a warning) from the
    *scenario* views used to sell something. This function answers only the second question.
    """
    return any(str(flag) in COMMERCIAL_CAUTION_FLAGS for flag in (risk_flags or []))


def domain_of(title: str, annotated: str | None = None) -> str:
    """Domain for a story: an explicit annotation wins, otherwise ``其他``.

    Nothing here infers a domain from a product catalogue. An honest ``其他`` beats a
    confident wrong label, and the plan forbids brand-driven relevance filtering outright.
    """
    if annotated and annotated in CATEGORIES:
        return annotated
    return "其他"


def diverse(candidates: list[dict], top_n: int = DIVERSITY["top_n"]) -> list[dict]:
    """Soft diversity over source and category, demoting rather than deleting.

    The plan allows the four views to overlap and forbids manufacturing differences by
    reshuffling. So this only swaps a candidate upward when the one it displaces is within
    ``max_candidate_score_gap`` - a genuinely better story is never demoted for variety.
    """
    ordered = sorted(candidates, key=lambda row: row.get("scenario_score", 0.0), reverse=True)
    if len(ordered) <= 1:
        return ordered[:top_n]
    gap = DIVERSITY["max_candidate_score_gap"]
    picked: list[dict] = []
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for row in ordered:
        if len(picked) >= top_n:
            break
        source = str(row.get("primary_source") or "")
        category = str(row.get("domain") or "其他")
        source_share = source_counts.get(source, 0) / max(1, len(picked))
        category_share = category_counts.get(category, 0) / max(1, len(picked))
        crowded = (source_share > DIVERSITY["soft_source_share"]
                   or category_share > DIVERSITY["soft_category_share"])
        if crowded and picked:
            worst = min(picked, key=lambda item: item.get("scenario_score", 0.0))
            if row.get("scenario_score", 0.0) - worst.get("scenario_score", 0.0) <= gap:
                # Not clearly better than what it would displace, so it waits its turn.
                continue
        picked.append(row)
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    if len(picked) < min(top_n, len(ordered)) and DIVERSITY["relax_if_insufficient"]:
        chosen = {id(row) for row in picked}
        for row in ordered:
            if len(picked) >= top_n:
                break
            if id(row) not in chosen:
                picked.append(row)
    return picked[:top_n]


def explain(row: dict) -> dict:
    """Why this story is here: the plan requires an explainable reason, not a bare number."""
    return {"position_score": row.get("position_score"), "coverage": row.get("coverage"),
            "freshness": row.get("freshness"), "feature_fit": row.get("feature_fit"),
            "distinct_origins": row.get("distinct_origins"),
            "general_score": row.get("general_score"),
            "scenario_score": row.get("scenario_score"),
            "note": "通用分与场景分为可解释加权结果，不是平台官方热度"}