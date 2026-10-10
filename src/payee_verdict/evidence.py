"""Append-only, hash-chained decision evidence, with replay and policy diffs.

Each record stores the SHA-256 of the previous record, so editing, deleting or
reordering any past record breaks `verify()`. This is tamper-*evident*, not
tamper-proof: someone who can rewrite the whole file can rebuild the chain. Anchor the
head hash somewhere external to close that gap.

A record holds everything needed to explain and re-decide a payout without calling the
payment system again: the input hash, the normalisation trace (changed steps only), the
raw system response, the outcome and matcher rule, and the policy context and verdict.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from payee_verdict.models import (
    CheckResult,
    Decision,
    DecisionContext,
    Outcome,
    Payout,
    ReasonCode,
    System,
    TraceStep,
    Verdict,
)
from payee_verdict.policy import Policy, decide

GENESIS = "0" * 64


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_hash(payout: Payout) -> str:
    return sha256_hex(canonical_json(payout.model_dump(mode="json")))


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    at: datetime
    payout_id: str
    input_hash: str
    system: System
    context: DecisionContext
    normalisation: dict[str, tuple[TraceStep, ...]]
    raw_response: dict[str, Any]
    outcome: Outcome
    reason_code: ReasonCode | None
    rule_id: str
    matched_locally: bool
    account_holder_name_shown: str | None
    policy_id: str
    policy_version: str
    policy_rule_id: str
    verdict: Verdict
    fallback_applied: str | None
    payer_message: str
    prev_hash: str
    hash: str

    def body_hash(self) -> str:
        return sha256_hex(canonical_json(self.model_dump(mode="json", exclude={"hash"})))


def _changed_steps(steps: tuple[TraceStep, ...]) -> tuple[TraceStep, ...]:
    return tuple(s for s in steps if s.changed)


class EvidenceLog:
    def __init__(self, records: list[EvidenceRecord] | None = None) -> None:
        self._records: list[EvidenceRecord] = list(records or [])

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    @property
    def head(self) -> str:
        return self._records[-1].hash if self._records else GENESIS

    def __len__(self) -> int:
        return len(self._records)

    def append(
        self,
        *,
        payout: Payout,
        context: DecisionContext,
        check: CheckResult,
        decision: Decision,
        payer_message: str,
        at: datetime,
    ) -> EvidenceRecord:
        normalisation: dict[str, tuple[TraceStep, ...]] = {}
        if check.match is not None:
            if check.match.requested is not None:
                normalisation["requested"] = _changed_steps(check.match.requested.trace)
            if check.match.actual is not None:
                normalisation["actual"] = _changed_steps(check.match.actual.trace)
        fields: dict[str, Any] = {
            "seq": len(self._records),
            "at": at,
            "payout_id": payout.payout_id,
            "input_hash": input_hash(payout),
            "system": payout.system,
            "context": context,
            "normalisation": normalisation,
            "raw_response": check.raw_response,
            "outcome": check.outcome,
            "reason_code": check.reason_code,
            "rule_id": check.rule_id,
            "matched_locally": check.matched_locally,
            "account_holder_name_shown": check.account_holder_name,
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "policy_rule_id": decision.policy_rule_id,
            "verdict": decision.verdict,
            "fallback_applied": decision.fallback_applied,
            "payer_message": payer_message,
            "prev_hash": self.head,
            "hash": "",
        }
        draft = EvidenceRecord.model_validate(fields)
        record = draft.model_copy(update={"hash": draft.body_hash()})
        self._records.append(record)
        return record

    def verify(self) -> list[str]:
        """Return integrity problems; an empty list means the chain is intact."""
        problems: list[str] = []
        prev = GENESIS
        for i, r in enumerate(self._records):
            if r.seq != i:
                problems.append(f"record {i}: sequence number {r.seq}")
            if r.prev_hash != prev:
                problems.append(f"record {i}: prev_hash does not match previous record")
            if r.hash != r.body_hash():
                problems.append(f"record {i}: content does not match its hash")
            prev = r.hash
        return problems

    def to_jsonl(self) -> str:
        return "".join(r.model_dump_json() + "\n" for r in self._records)

    @classmethod
    def from_jsonl(cls, text: str) -> EvidenceLog:
        return cls([EvidenceRecord.model_validate_json(line) for line in text.splitlines() if line])


class ReplayItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    payout_id: str
    recorded_verdict: Verdict
    recorded_rule: str
    replayed_verdict: Verdict
    replayed_rule: str

    @property
    def changed(self) -> bool:
        return self.recorded_verdict is not self.replayed_verdict


class ReplayReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str
    policy_version: str
    recorded_policies: tuple[str, ...]
    chain_problems: tuple[str, ...]
    items: tuple[ReplayItem, ...]

    @property
    def changed(self) -> tuple[ReplayItem, ...]:
        return tuple(i for i in self.items if i.changed)

    @property
    def transitions(self) -> dict[tuple[Verdict, Verdict], int]:
        counts = Counter((i.recorded_verdict, i.replayed_verdict) for i in self.changed)
        return dict(sorted(counts.items()))


def replay(log: EvidenceLog, policy: Policy) -> ReplayReport:
    """Re-decide every record under `policy` from its stored context.

    No payment system is called: the stored outcome is re-used, so only the policy can
    change the verdict. Under the policy that produced the log, every verdict must be
    identical; anything else is a determinism bug.
    """
    items = []
    for r in log.records:
        d = decide(r.context, policy)
        items.append(
            ReplayItem(
                seq=r.seq,
                payout_id=r.payout_id,
                recorded_verdict=r.verdict,
                recorded_rule=r.policy_rule_id,
                replayed_verdict=d.verdict,
                replayed_rule=d.policy_rule_id,
            )
        )
    recorded = sorted({f"{r.policy_id}@{r.policy_version}" for r in log.records})
    return ReplayReport(
        policy_id=policy.id,
        policy_version=policy.version,
        recorded_policies=tuple(recorded),
        chain_problems=tuple(log.verify()),
        items=tuple(items),
    )


def render_diff(report: ReplayReport, *, limit: int = 20) -> str:
    """Markdown report of which verdicts change under the replayed policy."""
    total = len(report.items)
    lines = [
        f"# Policy diff: {', '.join(report.recorded_policies) or '(empty log)'} -> "
        f"{report.policy_id}@{report.policy_version}",
        "",
        f"- Records replayed: {total}",
        f"- Verdicts unchanged: {total - len(report.changed)}",
        f"- Verdicts changed: {len(report.changed)}",
        f"- Evidence chain: {'intact' if not report.chain_problems else 'BROKEN'}",
        "",
    ]
    if report.transitions:
        lines += ["| From | To | Count |", "|---|---|---:|"]
        lines += [f"| {a.value} | {b.value} | {n} |" for (a, b), n in report.transitions.items()]
        lines += ["", "| Seq | Payout | Recorded (rule) | Replayed (rule) |", "|---:|---|---|---|"]
        for i in report.changed[:limit]:
            lines.append(
                f"| {i.seq} | {i.payout_id} | {i.recorded_verdict.value} ({i.recorded_rule}) "
                f"| {i.replayed_verdict.value} ({i.replayed_rule}) |"
            )
        if len(report.changed) > limit:
            lines.append(f"\n…and {len(report.changed) - limit} more.")
    for p in report.chain_problems:
        lines.append(f"- chain problem: {p}")
    return "\n".join(lines) + "\n"
