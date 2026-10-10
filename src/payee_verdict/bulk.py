"""Bulk mode: a file of payouts -> verdicts, aggregated counts and one evidence log.

Also generates synthetic payout files for load tests, built only from the synthetic
registry plus deterministic, seeded variations of the registered names.
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections import Counter
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from payee_verdict.adapters.base import Adapter
from payee_verdict.engine import Clock, utc_now, verify_payout
from payee_verdict.evidence import EvidenceLog
from payee_verdict.models import Outcome, PayeeType, Payout, System, Verdict
from payee_verdict.policy import Policy
from payee_verdict.registry import Registry

CSV_FIELDS = (
    "payout_id",
    "system",
    "account",
    "payee_name",
    "payee_type",
    "amount",
    "currency",
    "payee_id_code",
)


class BulkInputError(ValueError):
    pass


class BulkSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    by_outcome: dict[str, int]
    by_verdict: dict[str, int]
    by_system_outcome: dict[str, dict[str, int]]
    by_rule: dict[str, int]
    fallbacks_applied: int


def _rows_to_payouts(rows: Iterable[dict[str, Any]], *, bulk: bool) -> list[Payout]:
    payouts = []
    for n, row in enumerate(rows, start=1):
        data = {k: v for k, v in row.items() if v not in (None, "")}
        data["bulk"] = bulk
        try:
            payouts.append(Payout.model_validate(data))
        except ValidationError as exc:
            raise BulkInputError(f"row {n}: {exc}") from exc
    return payouts


def read_payouts(path: str | Path, *, bulk: bool = True) -> list[Payout]:
    """Read CSV (header row required) or JSONL, chosen by file extension."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".jsonl", ".ndjson"}:
        try:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise BulkInputError(f"{p}: invalid JSON line: {exc}") from exc
        return _rows_to_payouts(rows, bulk=bulk)
    return _rows_to_payouts(csv.DictReader(io.StringIO(text)), bulk=bulk)


def run_bulk(
    payouts: Iterable[Payout],
    adapters: Mapping[System, Adapter],
    policy: Policy,
    log: EvidenceLog,
    *,
    clock: Clock = utc_now,
) -> BulkSummary:
    outcomes: Counter[str] = Counter()
    verdicts: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    per_system: dict[str, Counter[str]] = {}
    fallbacks = 0
    total = 0
    for payout in payouts:
        v = verify_payout(payout, adapters, policy, log, clock=clock)
        total += 1
        outcomes[v.check.outcome.value] += 1
        verdicts[v.decision.verdict.value] += 1
        rules[v.check.rule_id] += 1
        per_system.setdefault(payout.system.value, Counter())[v.check.outcome.value] += 1
        fallbacks += v.decision.fallback_applied is not None
    return BulkSummary(
        total=total,
        by_outcome={o.value: outcomes[o.value] for o in Outcome},
        by_verdict={x.value: verdicts[x.value] for x in Verdict},
        by_system_outcome={
            s: {o.value: c[o.value] for o in Outcome} for s, c in sorted(per_system.items())
        },
        by_rule=dict(sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))),
        fallbacks_applied=fallbacks,
    )


def render_summary(summary: BulkSummary) -> str:
    outcomes = list(Outcome)
    lines = [f"payouts: {summary.total}", ""]
    lines.append(f"{'system':<16}" + "".join(f"{o.value:>14}" for o in outcomes))
    for system, row in summary.by_system_outcome.items():
        lines.append(f"{system:<16}" + "".join(f"{row[o.value]:>14}" for o in outcomes))
    lines.append(f"{'TOTAL':<16}" + "".join(f"{summary.by_outcome[o.value]:>14}" for o in outcomes))
    lines.append("")
    lines.append("verdicts: " + ", ".join(f"{k} {v}" for k, v in summary.by_verdict.items()))
    lines.append(f"fallbacks applied: {summary.fallbacks_applied}")
    lines.append(
        "top rules: " + ", ".join(f"{k} {v}" for k, v in list(summary.by_rule.items())[:8])
    )
    return "\n".join(lines) + "\n"


# --- synthetic payout generation ------------------------------------------------------

_CURRENCY = {
    System.EU_VOP: "EUR",
    System.UK_COP: "GBP",
    System.IN_NAME_LOOKUP: "INR",
    System.BR_PIX: "BRL",
}
_AMOUNT_RANGE = {
    "EUR": (5, 25_000),
    "GBP": (5, 25_000),
    "INR": (500, 2_500_000),
    "BRL": (10, 120_000),
}
_OTHER_NAMES = (
    "Halloran Verity",
    "Osgood Plumbline Ltd",
    "Fenwick Ashdown",
    "Tarrant Quarry Works Ltd",
    "Lumen Okafor-Reyes",
)


def _variant(name: str, rng: random.Random) -> str:
    roll = rng.random()
    tokens = name.split()
    if roll < 0.55:
        return name
    if roll < 0.65:
        return name.upper()
    if roll < 0.75 and len(name) > 6:
        i = rng.randrange(1, len(name) - 2)
        return name[:i] + name[i + 1] + name[i] + name[i + 2 :]
    if roll < 0.82 and len(tokens) >= 2:
        return " ".join([tokens[-1], *tokens[:-1]])
    if roll < 0.88 and len(tokens) >= 2:
        return " ".join([tokens[0][0], *tokens[1:]])
    if roll < 0.92:
        return name + ", 12 Example Street"
    return rng.choice(_OTHER_NAMES)


def generate_payouts(registry: Registry, count: int, *, seed: int = 7) -> list[dict[str, str]]:
    """Deterministic synthetic payouts (as CSV-ready dicts) against the registry."""
    rng = random.Random(seed)
    pool: list[tuple[System, str, str, PayeeType]] = []
    pool += [(System.EU_VOP, a.iban, a.name, a.type) for a in registry.eu_vop]
    pool += [(System.UK_COP, a.account, a.name, a.type) for a in registry.uk_cop]
    pool += [(System.IN_NAME_LOOKUP, a.account, a.name, a.type) for a in registry.in_name_lookup]
    pool += [(System.BR_PIX, k.key, k.name, k.type) for k in registry.br_pix]
    rows = []
    for i in range(count):
        system, account, name, ptype = rng.choice(pool)
        if rng.random() < 0.01:
            account = account + "9"  # unknown account
        currency = _CURRENCY[system]
        lo, hi = _AMOUNT_RANGE[currency]
        amount = Decimal(rng.randint(lo * 100, hi * 100)) / 100
        rows.append(
            {
                "payout_id": f"PO-{i + 1:06d}",
                "system": system.value,
                "account": account,
                "payee_name": _variant(name, rng),
                "payee_type": ptype.value,
                "amount": f"{amount:.2f}",
                "currency": currency,
                "payee_id_code": "",
            }
        )
    return rows


def write_csv(rows: list[dict[str, str]], path: str | Path) -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    Path(path).write_text(buf.getvalue(), encoding="utf-8", newline="\n")
