"""The requester-side pipeline: payout -> system check -> policy verdict -> evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from payee_verdict.adapters.base import Adapter
from payee_verdict.evidence import EvidenceLog, EvidenceRecord
from payee_verdict.messages import payer_message
from payee_verdict.models import CheckResult, Decision, DecisionContext, Payout, System
from payee_verdict.policy import Policy, decide

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Verification(BaseModel):
    model_config = ConfigDict(frozen=True)

    payout: Payout
    check: CheckResult
    decision: Decision
    payer_message: str
    record: EvidenceRecord


class EngineError(ValueError):
    pass


def context_for(payout: Payout, check: CheckResult) -> DecisionContext:
    return DecisionContext(
        system=payout.system,
        outcome=check.outcome,
        reason_code=check.reason_code,
        amount=payout.amount,
        currency=payout.currency,
        payee_type=payout.payee_type,
        bulk=payout.bulk,
    )


def verify_payout(
    payout: Payout,
    adapters: Mapping[System, Adapter],
    policy: Policy,
    log: EvidenceLog,
    *,
    clock: Clock = utc_now,
) -> Verification:
    adapter = adapters.get(payout.system)
    if adapter is None:
        raise EngineError(f"no adapter configured for {payout.system.value}")
    check = adapter.check(payout)
    ctx = context_for(payout, check)
    decision = decide(ctx, policy)
    message = payer_message(check)
    record = log.append(
        payout=payout,
        context=ctx,
        check=check,
        decision=decision,
        payer_message=message,
        at=clock(),
    )
    return Verification(
        payout=payout, check=check, decision=decision, payer_message=message, record=record
    )
