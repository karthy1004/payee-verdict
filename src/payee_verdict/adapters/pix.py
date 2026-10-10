"""Simulated Brazil PIX key lookup.

Semantics modelled: a payment key (email, phone or random key) resolves to an account
holder's name and holder type, which this engine matches locally; an unknown key cannot
be checked; a key carrying a fraud flag returns no name and is NOT_POSSIBLE / KEY_FLAGGED.

The response shape is this project's own, not the BCB DICT API. Details of real DICT
behaviour come from BCB slide decks and vendor material and are unverified here.
"""

from __future__ import annotations

from payee_verdict.adapters.base import from_match, not_possible
from payee_verdict.matcher import match_names
from payee_verdict.models import CheckResult, PayeeType, Payout, ReasonCode, System
from payee_verdict.registry import PixKey

_HOLDER = {PayeeType.PERSON: "NATURAL_PERSON", PayeeType.ENTITY: "LEGAL_PERSON"}


class PixKeyAdapter:
    system = System.BR_PIX

    def __init__(self, keys: tuple[PixKey, ...]) -> None:
        self._keys = {k.key.strip().lower(): k for k in keys}

    def check(self, payout: Payout) -> CheckResult:
        entry = self._keys.get(payout.account.strip().lower())
        if entry is None:
            return not_possible(self.system, ReasonCode.KEY_NOT_FOUND, {"key_status": "NOT_FOUND"})
        if entry.flagged:
            return not_possible(
                self.system,
                ReasonCode.KEY_FLAGGED,
                {"key_status": "FLAGGED_FOR_FRAUD", "key_type": entry.key_type},
            )
        raw = {
            "key_status": "ACTIVE",
            "key_type": entry.key_type,
            "holder_type": _HOLDER[entry.type],
            "holder_name": entry.name,
        }
        match = match_names(payout.payee_name, entry.name, entry.type)
        return from_match(self.system, match, entry.name, raw, matched_locally=True)
