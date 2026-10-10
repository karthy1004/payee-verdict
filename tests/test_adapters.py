from __future__ import annotations

from payee_verdict.adapters import EuVopAdapter, IndiaNameLookupAdapter, PixKeyAdapter, UkCopAdapter
from payee_verdict.messages import payer_message
from payee_verdict.models import CheckResult, Outcome, PayeeType, ReasonCode, System
from payee_verdict.registry import Registry
from tests.conftest import payout

E, P = PayeeType.ENTITY, PayeeType.PERSON


def test_eu_unknown_account(registry: Registry) -> None:
    r = EuVopAdapter(registry.eu_vop).check(payout(System.EU_VOP, "DE00TEST9999999999", "X"))
    assert (r.outcome, r.reason_code) == (Outcome.NOT_POSSIBLE, ReasonCode.ACCOUNT_NOT_FOUND)


def test_eu_close_match_returns_name_in_raw_and_result(registry: Registry) -> None:
    r = EuVopAdapter(registry.eu_vop).check(
        payout(System.EU_VOP, "DE00TEST0000000011", "Grünfeld Solar GmbH", E)
    )
    assert r.outcome is Outcome.CLOSE_MATCH
    assert r.account_holder_name == r.raw_response["name"] == "Grünfeld Solar AG"
    assert r.matched_locally is False


def test_eu_id_code_ignored_for_person_account(registry: Registry) -> None:
    r = EuVopAdapter(registry.eu_vop).check(
        payout(System.EU_VOP, "DE00TEST0000000004", "Ilse Brandtvogel", id_code="ANY")
    )
    assert r.raw_response["checked"] == "name"
    assert r.outcome is Outcome.MATCH


def test_eu_iban_lookup_ignores_spaces_and_case(registry: Registry) -> None:
    r = EuVopAdapter(registry.eu_vop).check(
        payout(System.EU_VOP, "de00 test 0000 0000 04", "Ilse Brandtvogel")
    )
    assert r.outcome is Outcome.MATCH


def test_eu_empty_name_is_not_possible(registry: Registry) -> None:
    r = EuVopAdapter(registry.eu_vop).check(payout(System.EU_VOP, "DE00TEST0000000004", "  "))
    assert (r.outcome, r.reason_code) == (Outcome.NOT_POSSIBLE, ReasonCode.EMPTY_NAME)
    assert "no usable name" in payer_message(r)


def test_uk_paths(registry: Registry) -> None:
    uk = UkCopAdapter(registry.uk_cop)
    missing = uk.check(payout(System.UK_COP, "99-99-99:00000000", "X"))
    assert missing.reason_code is ReasonCode.ACCOUNT_NOT_FOUND
    opted = uk.check(payout(System.UK_COP, "99-00-04:00000004", "Quillon Data Services", E))
    assert opted.reason_code is ReasonCode.OPTED_OUT
    wrong_type = uk.check(payout(System.UK_COP, "99-00-05:00000005", "Tobias Wrenfield", P))
    assert (wrong_type.outcome, wrong_type.rule_id) == (
        Outcome.CLOSE_MATCH,
        "ACCOUNT_TYPE_MISMATCH",
    )
    assert wrong_type.raw_response["account_type"] == "BUSINESS"
    assert "account type" in payer_message(wrong_type)
    right_type = uk.check(payout(System.UK_COP, "99-00-05:00000005", "Tobias Wrenfield", E))
    assert right_type.outcome is Outcome.MATCH
    assert right_type.reason_code is None


def test_uk_type_mismatch_on_no_match_stays_no_match(registry: Registry) -> None:
    r = UkCopAdapter(registry.uk_cop).check(
        payout(System.UK_COP, "99-00-05:00000005", "Someone Else", P)
    )
    assert r.outcome is Outcome.NO_MATCH
    assert r.reason_code is ReasonCode.ACCOUNT_TYPE_MISMATCH
    assert r.raw_response["returned_name"] is None


def test_india_invalid_account(registry: Registry) -> None:
    r = IndiaNameLookupAdapter(registry.in_name_lookup).check(
        payout(System.IN_NAME_LOOKUP, "TEST0000009:1", "X")
    )
    assert r.raw_response == {"status": "INVALID_ACCOUNT"}


def test_pix_paths(registry: Registry) -> None:
    pix = PixKeyAdapter(registry.br_pix)
    assert pix.check(payout(System.BR_PIX, "nobody@pix.example", "X")).reason_code is (
        ReasonCode.KEY_NOT_FOUND
    )
    ok = pix.check(payout(System.BR_PIX, "LOJA.AURORA@PIX.EXAMPLE ", "Aurora Doces Artesanais", E))
    assert (ok.outcome, ok.rule_id) == (Outcome.MATCH, "LEGAL_FORM_EQUIV")
    assert ok.matched_locally is True
    assert ok.raw_response["holder_type"] == "LEGAL_PERSON"


def test_not_possible_without_reason_has_generic_text() -> None:
    r = CheckResult(system=System.EU_VOP, outcome=Outcome.NOT_POSSIBLE, rule_id="X")
    assert "reason not given" in payer_message(r)


def test_match_message() -> None:
    r = CheckResult(system=System.EU_VOP, outcome=Outcome.MATCH, rule_id="X")
    assert payer_message(r) == "The name matches the account holder."
