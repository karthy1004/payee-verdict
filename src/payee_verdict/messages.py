"""Text shown to the payer for each outcome.

Wording is this project's own. Real schemes and banks publish their own required or
recommended wording; check that before reusing any of this.
"""

from __future__ import annotations

from payee_verdict.models import CheckResult, Outcome, ReasonCode

NO_MATCH_WARNING = (
    "Warning: the name you entered does not match the account holder. If you continue, "
    "the money may go to someone other than the payee you intend, and you may not be "
    "able to get it back."
)

NOT_POSSIBLE_WARNING = (
    "Warning: we could not check the payee's name ({reason}). If you continue, you are "
    "paying without confirmation that the account belongs to the payee you intend."
)

_REASON_TEXT: dict[ReasonCode, str] = {
    ReasonCode.TIMEOUT: "the payee's bank did not answer in time",
    ReasonCode.ACCOUNT_NOT_FOUND: "the account could not be found",
    ReasonCode.KEY_NOT_FOUND: "the payment key is not registered",
    ReasonCode.KEY_FLAGGED: "the payment key is flagged",
    ReasonCode.LOOKUP_UNAVAILABLE: "the name lookup service is unavailable",
    ReasonCode.OPTED_OUT: "the account does not take part in name checks",
    ReasonCode.EMPTY_NAME: "no usable name was provided",
}


def payer_message(check: CheckResult) -> str:
    if check.outcome is Outcome.MATCH:
        return "The name matches the account holder."
    if check.outcome is Outcome.CLOSE_MATCH:
        name = check.account_holder_name or "(not provided)"
        extra = ""
        if check.reason_code is ReasonCode.ACCOUNT_TYPE_MISMATCH:
            extra = " The account type (personal or business) is also different."
        return (
            f"The name you entered is close to, but not the same as, the account holder's "
            f"name: {name}.{extra} Check it is the payee you intend before you continue."
        )
    if check.outcome is Outcome.NO_MATCH:
        return NO_MATCH_WARNING
    reason = "reason not given"
    if check.reason_code is not None:
        reason = _REASON_TEXT.get(check.reason_code, reason)
    return NOT_POSSIBLE_WARNING.format(reason=reason)
