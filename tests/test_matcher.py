from __future__ import annotations

import pytest

from payee_verdict.matcher import (
    lei_check_digits_valid,
    lei_with_check_digits,
    levenshtein,
    match_identifier,
    match_names,
    similarity,
)
from payee_verdict.models import Outcome, PayeeType
from payee_verdict.normalize import normalize

P, E = PayeeType.PERSON, PayeeType.ENTITY


@pytest.mark.parametrize(
    ("a", "b", "d"),
    [("", "", 0), ("abc", "", 3), ("", "ab", 2), ("kitten", "sitting", 3), ("same", "same", 0)],
)
def test_levenshtein(a: str, b: str, d: int) -> None:
    assert levenshtein(a, b) == d


def test_similarity_of_empty_strings_is_one() -> None:
    assert similarity("", "") == 1.0


@pytest.mark.parametrize(
    ("req", "act", "ptype", "outcome", "rule"),
    [
        ("Ilse Brandtvogel", "Ilse Brandtvogel", P, Outcome.MATCH, "EXACT_NORMALISED"),
        ("Grunfeld Ilse", "Grünfeld Ilse", P, Outcome.MATCH, "TRANSLITERATION_EQUIV"),
        ("Ilse Brandt-vogel", "Ilse Brandtvogel", P, Outcome.MATCH, "SPACING_EQUIV"),
        ("BlueFjord Bakery", "Blue Fjord Bakery Ltd", E, Outcome.MATCH, "SPACING_EQUIV"),
        ("Muller Bau", "Müller Bau GmbH", E, Outcome.MATCH, "TRANSLITERATION_EQUIV"),
        ("Muller Bau AG", "Müller Bau GmbH", E, Outcome.CLOSE_MATCH, "LEGAL_FORM_CONFLICT"),
        ("Brandtvogel Ilse", "Ilse Brandtvogel", P, Outcome.CLOSE_MATCH, "TOKEN_REORDER"),
        ("Bakery Blue Fjord", "Blue Fjord Bakery", E, Outcome.CLOSE_MATCH, "TOKEN_REORDER"),
        ("I Brandtvogel", "Ilse Brandtvogel", P, Outcome.CLOSE_MATCH, "INITIALS"),
        ("Ilse Brandtvogel", "I Brandtvogel", P, Outcome.CLOSE_MATCH, "INITIALS"),
        (
            "Ilse Brandtvogel",
            "Ilse Marlene Brandtvogel",
            P,
            Outcome.CLOSE_MATCH,
            "MISSING_MIDDLE_NAME",
        ),
        ("A X Teerthan", "Anirudh Bhaskar Teerthan", P, Outcome.NO_MATCH, "PARTIAL_TOKEN_OVERLAP"),
        (
            "Rosalind Featherstoneh",
            "Rosalind Featherstonehaugh",
            P,
            Outcome.CLOSE_MATCH,
            "TRUNCATION",
        ),
        ("Ilse Brantvogel", "Ilse Brandtvogel", P, Outcome.CLOSE_MATCH, "EDIT_DISTANCE_CLOSE"),
        ("Brantvogel Ilse", "Ilse Brandtvogel", P, Outcome.CLOSE_MATCH, "EDIT_DISTANCE_CLOSE"),
        ("Blue Fiord Bakery", "Blue Fjord Bakery", E, Outcome.CLOSE_MATCH, "EDIT_DISTANCE_CLOSE"),
        ("Ana Lind", "Ilse Brandtvogel", P, Outcome.NO_MATCH, "NO_TOKEN_OVERLAP"),
        ("Osgood Ltd", "Blue Fjord Bakery Ltd", E, Outcome.NO_MATCH, "NO_TOKEN_OVERLAP"),
        ("", "Ilse Brandtvogel", P, Outcome.NOT_POSSIBLE, "EMPTY_NAME"),
        ("Ilse, 4 Main St", "Ilse", P, Outcome.CLOSE_MATCH, "ADDRESS_IN_NAME"),
    ],
)
def test_rules(req: str, act: str, ptype: PayeeType, outcome: Outcome, rule: str) -> None:
    r = match_names(req, act, ptype)
    assert (r.outcome, r.rule_id) == (outcome, rule), r.explanation


def test_surname_only_is_not_enough() -> None:
    r = match_names("Brandtvogel", "Ilse Brandtvogel", P)
    assert r.outcome is Outcome.NO_MATCH


def test_single_token_person_does_not_align() -> None:
    assert match_names("Ilse", "Ilse Brandtvogel", P).outcome is Outcome.NO_MATCH


def test_match_result_explains_and_carries_both_traces() -> None:
    r = match_names("Blue Fjord Bakery Limited", "Blue Fjord Bakery Ltd", E)
    assert r.requested is not None and r.actual is not None
    assert "LIMITED vs LTD" in r.explanation
    assert r.requested.legal_form == r.actual.legal_form == "LTD"


def test_lei_helpers() -> None:
    lei = lei_with_check_digits("9999TESTPAYEE00001")
    assert lei == "9999TESTPAYEE0000195"
    assert lei_check_digits_valid(lei)
    assert not lei_check_digits_valid("9999TESTPAYEE0000196")
    assert not lei_check_digits_valid("SHORT")
    assert not lei_check_digits_valid("9999TESTPAYEE00001XX")
    with pytest.raises(ValueError, match="18 alphanumeric"):
        lei_with_check_digits("too-short")


@pytest.mark.parametrize(
    ("code", "codes", "rule"),
    [
        ("9999TESTPAYEE0000195", {"LEI": "9999TESTPAYEE0000195"}, "ID_EXACT"),
        ("de-test-000001", {"VAT": "DE-TEST-000001"}, "ID_EXACT"),
        ("9999TESTPAYEE0000196", {"LEI": "9999TESTPAYEE0000195"}, "ID_INVALID_FORMAT"),
        ("DE-TEST-000002", {"VAT": "DE-TEST-000001"}, "ID_MISMATCH"),
        ("DE-TEST-000001", {}, "ID_NOT_ON_FILE"),
    ],
)
def test_identifier_rules(code: str, codes: dict[str, str], rule: str) -> None:
    r = match_identifier(code, codes)
    assert r.rule_id == rule
    assert r.outcome in {Outcome.MATCH, Outcome.NO_MATCH}


def test_normalise_unknown_type_strips_honorific_and_legal_form() -> None:
    n = normalize("Dr Example Holdings Ltd")
    assert n.core_tokens == ("EXAMPLE", "HOLDINGS")
    assert n.legal_form == "LTD"
