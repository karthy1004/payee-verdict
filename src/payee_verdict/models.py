"""Shared types: outcomes, payouts, normalised names, check results and decisions."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Outcome(StrEnum):
    """The four outcomes every name-check system is mapped to."""

    MATCH = "MATCH"
    CLOSE_MATCH = "CLOSE_MATCH"
    NO_MATCH = "NO_MATCH"
    NOT_POSSIBLE = "NOT_POSSIBLE"


class PayeeType(StrEnum):
    PERSON = "PERSON"
    ENTITY = "ENTITY"


class System(StrEnum):
    """Simulated name-check systems. None of these talks to a real network."""

    EU_VOP = "EU_VOP"
    UK_COP = "UK_COP"
    IN_NAME_LOOKUP = "IN_NAME_LOOKUP"
    BR_PIX = "BR_PIX"


class Verdict(StrEnum):
    PROCEED = "PROCEED"
    WARN_CONFIRM = "WARN_CONFIRM"
    HOLD = "HOLD"


class ReasonCode(StrEnum):
    """Why a check could not be completed, or a qualifier on its outcome."""

    TIMEOUT = "TIMEOUT"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    KEY_NOT_FOUND = "KEY_NOT_FOUND"
    KEY_FLAGGED = "KEY_FLAGGED"
    LOOKUP_UNAVAILABLE = "LOOKUP_UNAVAILABLE"
    OPTED_OUT = "OPTED_OUT"
    ACCOUNT_TYPE_MISMATCH = "ACCOUNT_TYPE_MISMATCH"
    EMPTY_NAME = "EMPTY_NAME"


class AmountBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TraceStep(BaseModel):
    """One normalisation step: what it did to the string."""

    model_config = ConfigDict(frozen=True)

    step: str
    before: str
    after: str

    @property
    def changed(self) -> bool:
        return self.before != self.after


class NormalisedName(BaseModel):
    """Result of `normalize()`. `key` is what exact comparison uses."""

    model_config = ConfigDict(frozen=True)

    original: str
    key: str
    core_tokens: tuple[str, ...]
    folded_core: str
    legal_form: str | None
    legal_form_surface: str | None
    address_removed: str | None
    trace: tuple[TraceStep, ...]

    @property
    def core(self) -> str:
        return " ".join(self.core_tokens)


class MatchResult(BaseModel):
    """Output of the reference matcher. `rule_id` names the rule that decided."""

    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    rule_id: str
    similarity: float
    explanation: str
    requested: NormalisedName | None = None
    actual: NormalisedName | None = None


class Payout(BaseModel):
    """One payout instruction as the paying platform sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payout_id: str = Field(min_length=1)
    system: System
    account: str = Field(min_length=1)
    payee_name: str
    payee_type: PayeeType
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    bulk: bool = False
    payee_id_code: str | None = None

    @field_validator("payee_id_code")
    @classmethod
    def _blank_id_is_none(cls, v: str | None) -> str | None:
        return v if v and v.strip() else None


class CheckResult(BaseModel):
    """One system's answer, mapped onto the shared outcome model."""

    model_config = ConfigDict(frozen=True)

    system: System
    outcome: Outcome
    rule_id: str
    reason_code: ReasonCode | None = None
    account_holder_name: str | None = None
    matched_locally: bool = False
    latency_ms: int = 0
    raw_response: dict[str, Any] = Field(default_factory=dict)
    match: MatchResult | None = None


class DecisionContext(BaseModel):
    """Everything the policy looks at. Stored in evidence so decisions can be replayed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system: System
    outcome: Outcome
    reason_code: ReasonCode | None
    amount: Decimal
    currency: str
    payee_type: PayeeType
    bulk: bool


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    policy_id: str
    policy_version: str
    policy_rule_id: str
    amount_band: AmountBand
    fallback_applied: str | None = None
