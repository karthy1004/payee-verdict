from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from payee_verdict.models import (
    AmountBand,
    DecisionContext,
    Outcome,
    PayeeType,
    ReasonCode,
    System,
    Verdict,
)
from payee_verdict.policy import Policy, PolicyError, decide, load_policy
from tests.conftest import POLICY


def ctx(
    outcome: Outcome = Outcome.MATCH,
    *,
    system: System = System.EU_VOP,
    reason: ReasonCode | None = None,
    amount: str = "100",
    currency: str = "EUR",
    bulk: bool = False,
) -> DecisionContext:
    return DecisionContext(
        system=system,
        outcome=outcome,
        reason_code=reason,
        amount=Decimal(amount),
        currency=currency,
        payee_type=PayeeType.ENTITY,
        bulk=bulk,
    )


def _raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    ("amount", "band"),
    [("999.99", AmountBand.LOW), ("1000", AmountBand.MEDIUM), ("10000", AmountBand.HIGH)],
)
def test_band_edges(policy: Policy, amount: str, band: AmountBand) -> None:
    assert policy.band(Decimal(amount), "EUR") is band


@pytest.mark.parametrize(
    ("context", "verdict", "rule"),
    [
        (ctx(Outcome.MATCH), Verdict.PROCEED, "R02-match-proceed"),
        (ctx(Outcome.CLOSE_MATCH), Verdict.WARN_CONFIRM, "R04-close-match-confirm"),
        (
            ctx(Outcome.CLOSE_MATCH, bulk=True, amount="50000"),
            Verdict.HOLD,
            "R03-close-match-bulk-high-hold",
        ),
        (
            ctx(Outcome.CLOSE_MATCH, bulk=True, amount="5000"),
            Verdict.WARN_CONFIRM,
            "R04-close-match-confirm",
        ),
        (ctx(Outcome.NO_MATCH), Verdict.HOLD, "R05-no-match-hold"),
        (
            ctx(Outcome.NOT_POSSIBLE, reason=ReasonCode.TIMEOUT),
            Verdict.WARN_CONFIRM,
            "R06-not-possible-low-confirm",
        ),
        (
            ctx(Outcome.NOT_POSSIBLE, reason=ReasonCode.TIMEOUT, amount="5000"),
            Verdict.HOLD,
            "R07-not-possible-hold",
        ),
        (
            ctx(Outcome.NOT_POSSIBLE, reason=ReasonCode.ACCOUNT_NOT_FOUND),
            Verdict.HOLD,
            "R07-not-possible-hold",
        ),
    ],
)
def test_default_policy_rules(
    policy: Policy, context: DecisionContext, verdict: Verdict, rule: str
) -> None:
    d = decide(context, policy)
    assert (d.verdict, d.policy_rule_id) == (verdict, rule)
    assert d.fallback_applied is None


def test_default_verdict_when_no_rule_matches() -> None:
    data = _raw()
    data["rules"] = [{"id": "only-match", "when": {"outcome": ["MATCH"]}, "verdict": "PROCEED"}]
    p = Policy.model_validate(data)
    d = decide(ctx(Outcome.NO_MATCH), p)
    assert (d.verdict, d.policy_rule_id) == (Verdict.HOLD, "DEFAULT")


def test_catch_all_rule_allowed_last() -> None:
    data = _raw()
    data["rules"].append({"id": "last", "when": {}, "verdict": "HOLD"})
    assert Policy.model_validate(data).rules[-1].when.is_catch_all


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["rules"].append(dict(d["rules"][0])), "duplicate rule ids"),
        (
            lambda d: d["rules"].insert(0, {"id": "all", "when": {}, "verdict": "HOLD"}),
            "can never fire",
        ),
        (lambda d: d["fallbacks"].append(dict(d["fallbacks"][0])), "more than one fallback"),
        (
            lambda d: d["amount_bands"].update({"eur": {"low_below": 1, "medium_below": 2}}),
            "ISO currency",
        ),
        (
            lambda d: d["amount_bands"].update({"USD": {"low_below": 5, "medium_below": 5}}),
            "greater than",
        ),
        (lambda d: d.update({"version": "1.0"}), "version"),
        (lambda d: d["rules"][0]["when"].update({"colour": ["red"]}), "colour"),
        (lambda d: d["rules"][0].update({"verdict": "MAYBE"}), "verdict"),
    ],
)
def test_validation_on_load(mutate: Any, message: str, tmp_path: Path) -> None:
    data = _raw()
    mutate(data)
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PolicyError, match=message):
        load_policy(path)


def test_missing_file_and_unknown_currency(policy: Policy, tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="cannot load policy"):
        load_policy(tmp_path / "nope.yaml")
    with pytest.raises(PolicyError, match="no amount bands for USD"):
        decide(ctx(currency="USD"), policy)
