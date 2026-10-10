"""Requester-side decision policy: check outcome + payout context -> verdict.

The policy is versioned YAML, validated on load. Evaluation order:

1. Fallbacks: if a (system, reason code) pair has a configured fallback, it decides and
   the decision records ``fallback_applied``.
2. Rules, in file order; the first rule whose ``when`` block matches decides.
3. ``default_verdict`` if nothing matched.

`decide()` is a pure function of (DecisionContext, Policy): no clock, no I/O. That is what
makes replay and policy diffs possible.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from payee_verdict.models import (
    AmountBand,
    Decision,
    DecisionContext,
    Outcome,
    PayeeType,
    ReasonCode,
    System,
    Verdict,
)


class PolicyError(ValueError):
    pass


class When(BaseModel):
    """Conditions are ANDed. A missing condition matches anything."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: tuple[System, ...] | None = None
    outcome: tuple[Outcome, ...] | None = None
    reason_code: tuple[ReasonCode, ...] | None = None
    amount_band: tuple[AmountBand, ...] | None = None
    payee_type: tuple[PayeeType, ...] | None = None
    bulk: bool | None = None

    @property
    def is_catch_all(self) -> bool:
        return all(getattr(self, f) is None for f in type(self).model_fields)

    def matches(self, ctx: DecisionContext, band: AmountBand) -> bool:
        return (
            (self.system is None or ctx.system in self.system)
            and (self.outcome is None or ctx.outcome in self.outcome)
            and (self.reason_code is None or ctx.reason_code in self.reason_code)
            and (self.amount_band is None or band in self.amount_band)
            and (self.payee_type is None or ctx.payee_type in self.payee_type)
            and (self.bulk is None or ctx.bulk is self.bulk)
        )


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    when: When
    verdict: Verdict
    note: str = ""


class Fallback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system: System
    reason_code: ReasonCode
    verdict: Verdict
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.system.value}/{self.reason_code.value} -> {self.verdict.value}"


class Bands(BaseModel):
    """Upper bounds (exclusive) of the low and medium bands, per currency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    low_below: Decimal = Field(gt=0)
    medium_below: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Bands:
        if self.medium_below <= self.low_below:
            raise ValueError("medium_below must be greater than low_below")
        return self


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = ""
    amount_bands: dict[str, Bands]
    fallbacks: tuple[Fallback, ...] = ()
    rules: tuple[Rule, ...] = Field(min_length=1)
    default_verdict: Verdict

    @model_validator(mode="after")
    def _consistent(self) -> Policy:
        ids = [r.id for r in self.rules]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate rule ids: {dupes}")
        for i, rule in enumerate(self.rules[:-1]):
            if rule.when.is_catch_all:
                raise ValueError(
                    f"rule {rule.id} matches everything, so the {len(self.rules) - i - 1} "
                    "rule(s) after it can never fire"
                )
        pairs = [(f.system, f.reason_code) for f in self.fallbacks]
        if len(pairs) != len(set(pairs)):
            raise ValueError("more than one fallback for the same system and reason code")
        for code in self.amount_bands:
            if len(code) != 3 or not code.isupper():
                raise ValueError(f"amount_bands key {code!r} is not an ISO currency code")
        return self

    def band(self, amount: Decimal, currency: str) -> AmountBand:
        bands = self.amount_bands.get(currency)
        if bands is None:
            raise PolicyError(f"policy {self.id}@{self.version} has no amount bands for {currency}")
        if amount < bands.low_below:
            return AmountBand.LOW
        if amount < bands.medium_below:
            return AmountBand.MEDIUM
        return AmountBand.HIGH


def decide(ctx: DecisionContext, policy: Policy) -> Decision:
    band = policy.band(ctx.amount, ctx.currency)
    for fb in policy.fallbacks:
        if fb.system is ctx.system and fb.reason_code is ctx.reason_code:
            return Decision(
                verdict=fb.verdict,
                policy_id=policy.id,
                policy_version=policy.version,
                policy_rule_id=f"FALLBACK:{fb.system.value}/{fb.reason_code.value}",
                amount_band=band,
                fallback_applied=fb.label,
            )
    for rule in policy.rules:
        if rule.when.matches(ctx, band):
            return Decision(
                verdict=rule.verdict,
                policy_id=policy.id,
                policy_version=policy.version,
                policy_rule_id=rule.id,
                amount_band=band,
            )
    return Decision(
        verdict=policy.default_verdict,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_rule_id="DEFAULT",
        amount_band=band,
    )


def load_policy(path: str | Path) -> Policy:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Policy.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise PolicyError(f"cannot load policy {path}: {exc}") from exc
