"""Objective, local-evidence-only model recommendation policy (EUF-7)."""

from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass
from typing import Iterable


MODEL_MEASUREMENT_FRESH_SECONDS = 30 * 24 * 60 * 60
FIT_LABELS = {
    "FITS": "Comfortable",
    "TIGHT": "Tight",
    "NO-FIT": "Does not fit",
}


@dataclass(frozen=True)
class RecommendationCandidate:
    key: str
    standard_layout: bool
    immutable_identity: bool
    fit_verdict: str | None
    support_tier: str
    architecture_compatible: bool
    inference_verified: bool
    measured_local: bool
    installed: bool
    active: bool


@dataclass(frozen=True)
class RecommendationDecision:
    key: str
    eligible: bool
    rank: int | None
    label: str | None
    reasons: tuple[str, ...]


def _rank_key(candidate: RecommendationCandidate) -> tuple[object, ...]:
    fit_rank = {"FITS": 0, "TIGHT": 1}.get(candidate.fit_verdict, 2)
    support_rank = {"supported": 0, "preview": 1}.get(
        candidate.support_tier, 2)
    return (
        0 if candidate.immutable_identity else 1,
        fit_rank,
        support_rank,
        0 if candidate.architecture_compatible else 1,
        0 if candidate.inference_verified else 1,
        0 if candidate.measured_local else 1,
        0 if candidate.installed else 1,
        0 if candidate.active else 1,
        candidate.key,
    )


class ModelRecommendationPolicy:
    """Rank eligibility facts lexicographically; never assign a quality score."""

    def evaluate(
        self, candidates: Iterable[RecommendationCandidate],
    ) -> dict[str, RecommendationDecision]:
        rows = tuple(candidates)
        if len(rows) > 256:
            raise ValueError("recommendation input exceeds 256 candidates")
        if len({row.key for row in rows}) != len(rows):
            raise ValueError("recommendation candidate keys must be unique")
        eligible = [
            row for row in rows
            if row.standard_layout
            and (not row.installed or row.immutable_identity)
            and row.architecture_compatible
            and row.fit_verdict in {"FITS", "TIGHT"}
            and row.support_tier == "supported"
        ]
        eligible.sort(key=_rank_key)
        ranks = {row.key: index + 1 for index, row in enumerate(eligible)}
        decisions: dict[str, RecommendationDecision] = {}
        for row in rows:
            reasons = []
            if not row.standard_layout:
                reasons.append("standard GGUF layout is not verified")
            if not row.immutable_identity:
                reasons.append("artifact identity will be resolved during installation")
            if row.fit_verdict == "FITS":
                reasons.append("comfortable fit for the selected workload")
            elif row.fit_verdict == "TIGHT":
                reasons.append("tight fit for the selected workload")
            elif row.fit_verdict == "NO-FIT":
                reasons.append("does not fit the selected workload")
            else:
                reasons.append("fit has not been established")
            if row.support_tier != "supported":
                reasons.append(f"support tier is {row.support_tier}")
            if not row.architecture_compatible:
                reasons.append("architecture compatibility is not established")
            if row.inference_verified:
                reasons.append("current runtime inference is verified")
            if row.measured_local:
                reasons.append("fresh local measurement is available")
            rank = ranks.get(row.key)
            label = None
            if rank == 1:
                label = (
                    "Recommended for this BC250"
                    if row.installed and row.immutable_identity
                    else "Recommended to install"
                )
            decisions[row.key] = RecommendationDecision(
                key=row.key,
                eligible=rank is not None,
                rank=rank,
                label=label,
                reasons=tuple(reasons),
            )
        return decisions


def fit_label(verdict: str | None) -> str:
    return FIT_LABELS.get(str(verdict or ""), "Not evaluated")


def evidence_is_fresh(
    recorded_at: str | None,
    *,
    now: _datetime.datetime | None = None,
) -> bool:
    if not recorded_at:
        return False
    try:
        value = _datetime.datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if value.tzinfo is None:
        return False
    current = now or _datetime.datetime.now(_datetime.timezone.utc)
    age = (current.astimezone(_datetime.timezone.utc)
           - value.astimezone(_datetime.timezone.utc)).total_seconds()
    return 0 <= age <= MODEL_MEASUREMENT_FRESH_SECONDS


__all__ = [
    "FIT_LABELS", "MODEL_MEASUREMENT_FRESH_SECONDS", "ModelRecommendationPolicy",
    "RecommendationCandidate", "RecommendationDecision", "evidence_is_fresh",
    "fit_label",
]
