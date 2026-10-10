"""Simulated EU Verification of Payee responder.

Semantics modelled: the payee's bank (responder) runs the match and returns one of four
outcomes; it returns the account holder's name only for a close match; a legal-entity
payee can be checked by an identifier instead of a name; and a response that does not
arrive within the time limit is treated as "verification not possible".

The response shape here is this project's own. It is not the EPC VoP inter-PSP API
schema; see the EPC document library for that.
"""

from __future__ import annotations

from collections.abc import Callable

from payee_verdict.adapters.base import from_match, not_possible
from payee_verdict.matcher import match_identifier, match_names
from payee_verdict.models import CheckResult, Outcome, PayeeType, Payout, ReasonCode, System
from payee_verdict.normalize import normalize_identifier
from payee_verdict.registry import EuAccount

DEFAULT_TIMEOUT_MS = 5000

LatencyModel = Callable[[EuAccount | None], int]


def registry_latency(account: EuAccount | None) -> int:
    return account.latency_ms if account else 120


class EuVopAdapter:
    system = System.EU_VOP

    def __init__(
        self,
        accounts: tuple[EuAccount, ...],
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        latency: LatencyModel = registry_latency,
    ) -> None:
        self._accounts = {normalize_identifier(a.iban): a for a in accounts}
        self._timeout_ms = timeout_ms
        self._latency = latency

    def check(self, payout: Payout) -> CheckResult:
        account = self._accounts.get(normalize_identifier(payout.account))
        elapsed = self._latency(account)
        if elapsed > self._timeout_ms:
            return not_possible(
                self.system,
                ReasonCode.TIMEOUT,
                {
                    "status": "NO_RESPONSE_IN_TIME",
                    "elapsed_ms": elapsed,
                    "limit_ms": self._timeout_ms,
                },
                elapsed,
            )
        if account is None:
            return not_possible(
                self.system,
                ReasonCode.ACCOUNT_NOT_FOUND,
                {"status": "ANSWERED", "result": "NOT_POSSIBLE", "reason": "UNKNOWN_ACCOUNT"},
                elapsed,
            )
        if payout.payee_id_code and account.type is PayeeType.ENTITY:
            match = match_identifier(payout.payee_id_code, account.id_codes)
            checked = "identifier"
        else:
            match = match_names(payout.payee_name, account.name, account.type)
            checked = "name"
        raw = {
            "status": "ANSWERED",
            "checked": checked,
            "result": match.outcome.value,
            "responder_rule": match.rule_id,
            "elapsed_ms": elapsed,
            "name": account.name if match.outcome is Outcome.CLOSE_MATCH else None,
        }
        return from_match(
            self.system, match, account.name, raw, matched_locally=False, latency_ms=elapsed
        )
