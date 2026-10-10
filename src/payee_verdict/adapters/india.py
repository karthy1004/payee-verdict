"""Simulated India RTGS/NEFT beneficiary-name lookup.

Semantics modelled: the lookup returns the registered beneficiary name and makes no
match decision, so this engine matches it locally against the name the payer entered.
When the lookup is unavailable (system outage or bank not supported) the adapter returns
NOT_POSSIBLE / LOOKUP_UNAVAILABLE and the policy's configured fallback decides; the
fallback is recorded in the evidence log.

The response shape is this project's own, not an NPCI or RBI interface.
"""

from __future__ import annotations

from payee_verdict.adapters.base import from_match, not_possible
from payee_verdict.matcher import match_names
from payee_verdict.models import CheckResult, Payout, ReasonCode, System
from payee_verdict.registry import InAccount


def _key(account: str) -> str:
    return account.replace(" ", "").upper()


class IndiaNameLookupAdapter:
    system = System.IN_NAME_LOOKUP

    def __init__(self, accounts: tuple[InAccount, ...], *, available: bool = True) -> None:
        self._accounts = {_key(a.account): a for a in accounts}
        self.available = available

    def check(self, payout: Payout) -> CheckResult:
        if not self.available:
            return not_possible(
                self.system,
                ReasonCode.LOOKUP_UNAVAILABLE,
                {"status": "SERVICE_UNAVAILABLE"},
            )
        account = self._accounts.get(_key(payout.account))
        if account is None:
            return not_possible(
                self.system, ReasonCode.ACCOUNT_NOT_FOUND, {"status": "INVALID_ACCOUNT"}
            )
        if not account.lookup_supported:
            return not_possible(
                self.system,
                ReasonCode.LOOKUP_UNAVAILABLE,
                {"status": "BANK_NOT_LIVE_FOR_LOOKUP"},
            )
        raw = {"status": "OK", "beneficiary_name": account.name}
        match = match_names(payout.payee_name, account.name, payout.payee_type)
        return from_match(self.system, match, account.name, raw, matched_locally=True)
