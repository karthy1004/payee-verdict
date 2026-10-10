"""Adapter protocol and helpers shared by the simulated systems."""

from __future__ import annotations

from typing import Any, Protocol

from payee_verdict.models import CheckResult, MatchResult, Outcome, Payout, ReasonCode, System


class Adapter(Protocol):
    """Anything that can answer "is this the right payee?" for one system."""

    system: System

    def check(self, payout: Payout) -> CheckResult: ...


def not_possible(
    system: System, reason: ReasonCode, raw: dict[str, Any], latency_ms: int = 0
) -> CheckResult:
    return CheckResult(
        system=system,
        outcome=Outcome.NOT_POSSIBLE,
        rule_id=f"SYSTEM_{reason.value}",
        reason_code=reason,
        latency_ms=latency_ms,
        raw_response=raw,
    )


def from_match(
    system: System,
    match: MatchResult,
    holder_name: str,
    raw: dict[str, Any],
    *,
    matched_locally: bool,
    latency_ms: int = 0,
    reason: ReasonCode | None = None,
) -> CheckResult:
    """Map a matcher result onto a CheckResult.

    The account holder's name is exposed only for CLOSE_MATCH, so the payer can compare.
    It is never exposed for NO_MATCH: that would turn the check into a name-lookup oracle.
    """
    if reason is None and match.outcome is Outcome.NOT_POSSIBLE:
        reason = ReasonCode.EMPTY_NAME
    return CheckResult(
        system=system,
        outcome=match.outcome,
        rule_id=match.rule_id,
        reason_code=reason,
        account_holder_name=holder_name if match.outcome is Outcome.CLOSE_MATCH else None,
        matched_locally=matched_locally,
        latency_ms=latency_ms,
        raw_response=raw,
        match=match,
    )
