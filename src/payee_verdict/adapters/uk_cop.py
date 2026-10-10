"""Simulated UK Confirmation of Payee responder.

Semantics modelled: the payee's bank runs the match; the payer also states whether the
account is personal or business, and a name that matches on the wrong account type comes
back as a close match with the account holder's name; an account that has opted out of
the service cannot be checked.

Reason codes are this project's own; Pay.UK's CoP standard defines the real ones.
"""

from __future__ import annotations

from payee_verdict.adapters.base import from_match, not_possible
from payee_verdict.matcher import match_names
from payee_verdict.models import CheckResult, Outcome, PayeeType, Payout, ReasonCode, System
from payee_verdict.registry import UkAccount

_ACCOUNT_TYPE = {PayeeType.PERSON: "PERSONAL", PayeeType.ENTITY: "BUSINESS"}


def _key(account: str) -> str:
    return account.replace(" ", "")


class UkCopAdapter:
    system = System.UK_COP

    def __init__(self, accounts: tuple[UkAccount, ...]) -> None:
        self._accounts = {_key(a.account): a for a in accounts}

    def check(self, payout: Payout) -> CheckResult:
        account = self._accounts.get(_key(payout.account))
        if account is None:
            return not_possible(
                self.system, ReasonCode.ACCOUNT_NOT_FOUND, {"answer": "ACCOUNT_DOES_NOT_EXIST"}
            )
        if account.opted_out:
            return not_possible(self.system, ReasonCode.OPTED_OUT, {"answer": "ACCOUNT_OPTED_OUT"})
        match = match_names(payout.payee_name, account.name, account.type)
        type_mismatch = payout.payee_type is not account.type
        if match.outcome is Outcome.MATCH and type_mismatch:
            match = match.model_copy(
                update={
                    "outcome": Outcome.CLOSE_MATCH,
                    "rule_id": "ACCOUNT_TYPE_MISMATCH",
                    "explanation": f"name matches but this is a {_ACCOUNT_TYPE[account.type]} "
                    f"account, not {_ACCOUNT_TYPE[payout.payee_type]}",
                }
            )
        raw = {
            "answer": match.outcome.value,
            "account_type": _ACCOUNT_TYPE[account.type],
            "account_type_matches": not type_mismatch,
            "returned_name": account.name if match.outcome is Outcome.CLOSE_MATCH else None,
        }
        reason = ReasonCode.ACCOUNT_TYPE_MISMATCH if type_mismatch else None
        return from_match(
            self.system, match, account.name, raw, matched_locally=False, reason=reason
        )
