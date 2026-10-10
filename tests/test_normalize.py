from __future__ import annotations

import unicodedata

import pytest

from payee_verdict.models import PayeeType
from payee_verdict.normalize import normalize, normalize_identifier


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Straße", "STRASSE"),
        ("Søren", "SOEREN"),
        ("Åse Lindqværn", "AASE LINDQVAERN"),
        ("José García", "JOSE GARCIA"),
        ("Ciarán O'Dwyer", "CIARAN ODWYER"),
        ("Smith & Sons", "SMITH AND SONS"),
        ("  lots   of   space  ", "LOTS OF SPACE"),
    ],
)
def test_person_keys(raw: str, key: str) -> None:
    assert normalize(raw, PayeeType.PERSON).key == key


def test_decomposed_input_equals_composed() -> None:
    decomposed = unicodedata.normalize("NFD", "Müller")
    assert decomposed != "Müller"
    assert normalize(decomposed).key == normalize("Müller").key == "MUELLER"


def test_folded_core_drops_accents_without_transliterating() -> None:
    n = normalize("Müller Søren Straße", PayeeType.PERSON)
    assert n.key == "MUELLER SOEREN STRASSE"
    assert n.folded_core == "MULLER SOREN STRASSE"


@pytest.mark.parametrize(
    ("raw", "family", "core"),
    [
        ("Kaveri Agro Exports Pvt. Ltd.", "PVT_LTD", "KAVERI AGRO EXPORTS"),
        ("Kranzhofer Holzbau GmbH & Co. KG", "GMBH_CO_KG", "KRANZHOFER HOLZBAU"),
        ("Orsolina Ventimare S.p.A.", "SPA", "ORSOLINA VENTIMARE"),
        ("Vernoux S.A.R.L.", "SARL", "VERNOUX"),
        ("Ribeirão Verde S.A.", "SA", "RIBEIRAO VERDE"),
        ("The Blue Fjord Bakery", None, "BLUE FJORD BAKERY"),
        ("Ltd", None, "LTD"),  # a legal form alone is kept as the name
    ],
)
def test_entity_legal_forms(raw: str, family: str | None, core: str) -> None:
    n = normalize(raw, PayeeType.ENTITY)
    assert n.legal_form == family
    assert n.core == core


def test_person_type_never_strips_legal_forms_and_entity_keeps_titles() -> None:
    assert normalize("Ana Sa", PayeeType.PERSON).core == "ANA SA"
    assert normalize("Dr Example Ltd", PayeeType.ENTITY).core == "DR EXAMPLE"


def test_honorific_alone_is_kept() -> None:
    assert normalize("Mr", PayeeType.PERSON).core == "MR"


def test_address_removed_only_when_a_later_segment_has_a_digit() -> None:
    n = normalize("Ilse Brandtvogel, Hafenstraße 12, Bremen", PayeeType.PERSON)
    assert n.address_removed == "HAFENSTRASSE 12, BREMEN"
    assert n.core == "ILSE BRANDTVOGEL"
    assert normalize("Brandtvogel, Ilse", PayeeType.PERSON).address_removed is None


def test_trace_records_unchanged_steps_too() -> None:
    n = normalize("ILSE", PayeeType.PERSON)
    assert all(not s.changed for s in n.trace)
    assert [s.step for s in n.trace][-1] == "honorifics"


def test_entity_trace_has_noise_word_step() -> None:
    n = normalize("The Example Co", PayeeType.ENTITY)
    assert [s.step for s in n.trace][-2:] == ["legal_form", "noise_words"]


def test_normalize_identifier() -> None:
    assert normalize_identifier(" de-test.000/001 ") == "DETEST000001"
