"""One test (or more) per acceptance criterion in the v0.1 spec, named ac01..ac12."""

from __future__ import annotations

import itertools
import json
import time

import pytest

from payee_verdict.adapters import Adapter, EuVopAdapter, IndiaNameLookupAdapter, build_adapters
from payee_verdict.bulk import generate_payouts, run_bulk
from payee_verdict.corpus import load_corpus, run_corpus
from payee_verdict.engine import Clock, verify_payout
from payee_verdict.evidence import EvidenceLog, render_diff, replay
from payee_verdict.matcher import match_identifier, match_names
from payee_verdict.messages import NO_MATCH_WARNING
from payee_verdict.models import Outcome, PayeeType, Payout, ReasonCode, System, Verdict
from payee_verdict.normalize import normalize
from payee_verdict.policy import Policy
from payee_verdict.registry import Registry
from tests.conftest import CORPUS, payout

ALL_STEPS = [
    "unicode_nfc",
    "transliterate",
    "strip_diacritics",
    "uppercase",
    "strip_address",
    "ampersand",
    "punctuation",
    "whitespace",
    "honorifics",
    "legal_form",
]


# AC1 ------------------------------------------------------------------------------------
def test_ac01_mueller_gmbh_normalises_equal_with_full_trace() -> None:
    a = normalize("Müller GmbH")
    b = normalize("MUELLER GMBH")
    assert a.key == b.key == "MUELLER GMBH"
    assert a.legal_form == b.legal_form == "GMBH"
    assert [s.step for s in a.trace] == ALL_STEPS
    translit = next(s for s in a.trace if s.step == "transliterate")
    assert (translit.before, translit.after) == ("Müller GmbH", "Mueller GmbH")
    # every step's output is the next step's input: the trace is complete, not a summary
    for prev, nxt in itertools.pairwise(a.trace):
        assert prev.after == nxt.before
    assert match_names("Müller GmbH", "MUELLER GMBH", PayeeType.ENTITY).outcome is Outcome.MATCH


# AC2 ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("Blue Fjord Bakery Limited", "Blue Fjord Bakery Ltd"),
        ("Blue Fjord Bakery", "Blue Fjord Bakery Ltd"),
        ("Kaveri Agro Exports Private Limited", "Kaveri Agro Exports Pvt Ltd"),
        ("Tamsworth Pewter Public Limited Company", "Tamsworth Pewter PLC"),
        (
            "Lindenhof Kältetechnik Gesellschaft mit beschränkter Haftung",
            "Lindenhof Kältetechnik GmbH",
        ),
        ("Aurora Doces Artesanais Limitada", "Aurora Doces Artesanais Ltda"),
    ],
)
def test_ac02_legal_form_difference_alone_is_match_via_legal_form_equiv(
    requested: str, actual: str
) -> None:
    r = match_names(requested, actual, PayeeType.ENTITY)
    assert (r.outcome, r.rule_id) == (Outcome.MATCH, "LEGAL_FORM_EQUIV")


# AC3 ------------------------------------------------------------------------------------
PERSON_NAMES = [
    "Adaeze Okonkwo-Hale",
    "Ilse Brandtvogel",
    "José Peregrino Saltz",
    "Anirudh Bhaskar Teerthan",
    "Thaís Albuquerque Monteverde",
]


@pytest.mark.parametrize("name", PERSON_NAMES)
def test_ac03_transposed_person_name_is_close_match_never_match(name: str) -> None:
    tokens = normalize(name, PayeeType.PERSON).core_tokens
    for perm in itertools.permutations(tokens):
        if perm == tokens:
            continue
        r = match_names(" ".join(perm), name, PayeeType.PERSON)
        assert r.outcome is Outcome.CLOSE_MATCH, (perm, r)
        assert r.rule_id == "TOKEN_REORDER"


# AC4 ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code",
    [
        "9999TESTPAYEE0000874",  # correct
        "9999 TEST PAYEE 0000 874",  # correct, spaced
        "9999TESTPAYEE0000195",  # valid LEI, another company's
        "9999TESTPAYEE0000875",  # check digits wrong
        "DE-TEST-000001",  # VAT-style, not on file for this holder
        "",  # empty
        "???",
    ],
)
def test_ac04_id_code_check_returns_only_match_or_no_match(code: str, registry: Registry) -> None:
    holder = next(a for a in registry.eu_vop if a.iban == "IT00TEST0000000008")
    r = match_identifier(code, holder.id_codes)
    assert r.outcome in {Outcome.MATCH, Outcome.NO_MATCH}
    assert match_identifier(code, {}).outcome in {Outcome.MATCH, Outcome.NO_MATCH}


def test_ac04_id_code_through_eu_adapter(registry: Registry) -> None:
    adapter = EuVopAdapter(registry.eu_vop)
    ok = adapter.check(
        payout(
            System.EU_VOP,
            "IT00TEST0000000008",
            "",
            PayeeType.ENTITY,
            id_code="9999TESTPAYEE0000874",
        )
    )
    bad = adapter.check(
        payout(
            System.EU_VOP,
            "IT00TEST0000000008",
            "",
            PayeeType.ENTITY,
            id_code="9999TESTPAYEE0000195",
        )
    )
    assert (ok.outcome, ok.rule_id) == (Outcome.MATCH, "ID_EXACT")
    assert (bad.outcome, bad.rule_id) == (Outcome.NO_MATCH, "ID_MISMATCH")
    assert bad.account_holder_name is None


# AC5 ------------------------------------------------------------------------------------
def test_ac05_close_match_returns_holder_name_and_others_carry_warning(
    adapters: dict[System, Adapter], policy: Policy, clock: Clock
) -> None:
    log = EvidenceLog()
    close = verify_payout(
        payout(System.UK_COP, "99-00-01:00000001", "Okonkwo-Hale Adaeze"),
        adapters,
        policy,
        log,
        clock=clock,
    )
    assert close.check.outcome is Outcome.CLOSE_MATCH
    assert close.check.account_holder_name == "Adaeze Okonkwo-Hale"
    assert "Adaeze Okonkwo-Hale" in close.payer_message

    no = verify_payout(
        payout(System.UK_COP, "99-00-02:00000002", "Harrowgate Roofing Ltd", PayeeType.ENTITY),
        adapters,
        policy,
        log,
        clock=clock,
    )
    assert no.check.outcome is Outcome.NO_MATCH
    assert no.check.account_holder_name is None
    assert no.payer_message == NO_MATCH_WARNING
    assert "Harrowgate Tile" not in no.payer_message

    np_ = verify_payout(
        payout(System.UK_COP, "99-00-04:00000004", "Quillon Data Services Ltd", PayeeType.ENTITY),
        adapters,
        policy,
        log,
        clock=clock,
    )
    assert np_.check.outcome is Outcome.NOT_POSSIBLE
    assert np_.payer_message.startswith("Warning:")
    assert "does not take part" in np_.payer_message


# AC6 ------------------------------------------------------------------------------------
def test_ac06_eu_vop_response_over_5s_is_not_possible_timeout(registry: Registry) -> None:
    p = payout(System.EU_VOP, "DE00TEST0000000004", "Ilse Brandtvogel")
    slow = EuVopAdapter(registry.eu_vop, latency=lambda _acct: 5001).check(p)
    assert (slow.outcome, slow.reason_code) == (Outcome.NOT_POSSIBLE, ReasonCode.TIMEOUT)
    assert slow.rule_id == "SYSTEM_TIMEOUT"
    edge = EuVopAdapter(registry.eu_vop, latency=lambda _acct: 5000).check(p)
    assert edge.outcome is Outcome.MATCH
    # the registry's slow responder (7.2 s) times out with the default latency model
    default = EuVopAdapter(registry.eu_vop).check(
        payout(
            System.EU_VOP,
            "AT00TEST0000000007",
            "Kranzhofer Holzbau GmbH & Co. KG",
            PayeeType.ENTITY,
        )
    )
    assert default.reason_code is ReasonCode.TIMEOUT
    assert default.raw_response["elapsed_ms"] == 7200


def test_ac06_timeout_test_does_not_sleep(registry: Registry) -> None:
    started = time.perf_counter()
    EuVopAdapter(registry.eu_vop, latency=lambda _a: 60_000).check(
        payout(System.EU_VOP, "DE00TEST0000000004", "Ilse Brandtvogel")
    )
    assert time.perf_counter() - started < 1.0


# AC7 ------------------------------------------------------------------------------------
def test_ac07_flagged_pix_key_is_not_possible_and_default_policy_holds(
    adapters: dict[System, Adapter], policy: Policy, clock: Clock
) -> None:
    v = verify_payout(
        payout(
            System.BR_PIX,
            "00000000-0000-4000-8000-000000000002",
            "Caio Venâncio Delamare",
            amount="10",
        ),
        adapters,
        policy,
        EvidenceLog(),
        clock=clock,
    )
    assert (v.check.outcome, v.check.reason_code) == (Outcome.NOT_POSSIBLE, ReasonCode.KEY_FLAGGED)
    assert v.decision.verdict is Verdict.HOLD
    assert v.decision.policy_rule_id == "R01-flagged-key-hold"
    assert "holder_name" not in v.check.raw_response


# AC8 ------------------------------------------------------------------------------------
def test_ac08_india_lookup_name_is_matched_locally(registry: Registry) -> None:
    adapter = IndiaNameLookupAdapter(registry.in_name_lookup)
    r = adapter.check(
        payout(System.IN_NAME_LOOKUP, "TEST0000001:000000000001", "Venkatraman Priyadarsini")
    )
    assert r.matched_locally is True
    assert r.raw_response == {"status": "OK", "beneficiary_name": "Priyadarsini Venkatraman"}
    assert (r.outcome, r.rule_id) == (Outcome.CLOSE_MATCH, "TOKEN_REORDER")


@pytest.mark.parametrize(
    ("india_available", "account"),
    [(False, "TEST0000001:000000000001"), (True, "TEST0000004:000000000004")],
)
def test_ac08_india_lookup_unavailable_applies_and_records_fallback(
    india_available: bool,
    account: str,
    registry: Registry,
    policy: Policy,
    strict_policy: Policy,
    clock: Clock,
) -> None:
    adapters = build_adapters(registry, india_available=india_available)
    p = payout(System.IN_NAME_LOOKUP, account, "Anyone", PayeeType.ENTITY, amount="250000")
    log = EvidenceLog()
    v = verify_payout(p, adapters, policy, log, clock=clock)
    assert v.check.reason_code is ReasonCode.LOOKUP_UNAVAILABLE
    assert v.decision.fallback_applied == "IN_NAME_LOOKUP/LOOKUP_UNAVAILABLE -> WARN_CONFIRM"
    assert v.decision.verdict is Verdict.WARN_CONFIRM
    assert log.records[0].fallback_applied == v.decision.fallback_applied
    strict = verify_payout(p, adapters, strict_policy, log, clock=clock)
    assert strict.decision.verdict is Verdict.HOLD
    assert strict.record.fallback_applied == "IN_NAME_LOOKUP/LOOKUP_UNAVAILABLE -> HOLD"


# AC9 ------------------------------------------------------------------------------------
def _sample_log(adapters: dict[System, Adapter], policy: Policy, clock: Clock) -> EvidenceLog:
    log = EvidenceLog()
    cases = [
        payout(
            System.EU_VOP, "DE00TEST0000000001", "MUELLER GMBH", PayeeType.ENTITY, payout_id="A"
        ),
        payout(System.UK_COP, "99-00-01:00000001", "Okonkwo-Hale Adaeze", payout_id="B"),
        payout(System.BR_PIX, "00000000-0000-4000-8000-000000000002", "X", payout_id="C"),
        payout(
            System.IN_NAME_LOOKUP,
            "TEST0000002:000000000002",
            "Kaveri Agro Exports",
            PayeeType.ENTITY,
            payout_id="D",
        ),
    ]
    for p in cases:
        verify_payout(p, adapters, policy, log, clock=clock)
    return log


def test_ac09_evidence_record_is_complete(
    adapters: dict[System, Adapter], policy: Policy, clock: Clock
) -> None:
    log = _sample_log(adapters, policy, clock)
    rec = log.records[0]
    assert len(rec.input_hash) == 64
    assert rec.normalisation["actual"]  # trace of the registered name, changed steps only
    assert any(s.step == "transliterate" for s in rec.normalisation["actual"])
    assert rec.raw_response["status"] == "ANSWERED"
    assert (rec.outcome, rec.rule_id) == (Outcome.MATCH, "EXACT_NORMALISED")
    assert (rec.policy_id, rec.policy_version) == ("payout-default", "1.0.0")
    assert rec.verdict is Verdict.PROCEED
    assert rec.context.amount == payout(System.EU_VOP, "x", "y").amount
    assert rec.payer_message
    assert log.verify() == []


def test_ac09_hash_chain_detects_edit_delete_and_reorder(
    adapters: dict[System, Adapter], policy: Policy, clock: Clock
) -> None:
    log = _sample_log(adapters, policy, clock)
    lines = log.to_jsonl().splitlines()

    edited = json.loads(lines[2])
    edited["verdict"] = "PROCEED"
    tampered = EvidenceLog.from_jsonl("\n".join([*lines[:2], json.dumps(edited), *lines[3:]]))
    assert tampered.verify() == ["record 2: content does not match its hash"]

    deleted = EvidenceLog.from_jsonl("\n".join([lines[0], *lines[2:]]))
    assert any("prev_hash" in p for p in deleted.verify())

    reordered = EvidenceLog.from_jsonl("\n".join([lines[1], lines[0], *lines[2:]]))
    assert reordered.verify()


# AC10 -----------------------------------------------------------------------------------
def test_ac10_replay_same_policy_is_identical_and_new_policy_gives_diff(
    registry: Registry, policy: Policy, strict_policy: Policy, clock: Clock
) -> None:
    adapters = build_adapters(registry)
    payouts = [Payout.model_validate(r | {"bulk": True}) for r in generate_payouts(registry, 400)]
    log = EvidenceLog()
    run_bulk(payouts, adapters, policy, log, clock=clock)

    first = replay(log, policy)
    second = replay(EvidenceLog.from_jsonl(log.to_jsonl()), policy)
    assert first.changed == () and second.changed == ()
    assert [i.replayed_verdict for i in first.items] == [r.verdict for r in log.records]
    assert first == second

    diff = replay(log, strict_policy)
    assert diff.changed, "the strict policy must change at least one verdict"
    assert all(i.replayed_verdict is Verdict.HOLD for i in diff.changed)
    text = render_diff(diff)
    assert "payout-default@1.0.0 -> payout-default@2.0.0" in text
    assert f"Verdicts changed: {len(diff.changed)}" in text


# AC11 -----------------------------------------------------------------------------------
def test_ac11_corpus_reports_per_category_accuracy_and_meets_threshold() -> None:
    corpus, digest = load_corpus(CORPUS)
    assert len(corpus.cases) >= 150
    report = run_corpus(corpus, digest)
    categories = {s.category for s in report.by_category()}
    assert {"diacritics", "legal_form", "reorder_person", "initials", "typo", "truncation"} <= (
        categories
    )
    assert {"address_in_name", "different_entity", "different_person"} <= categories
    assert report.accuracy >= corpus.threshold
    assert report.unsafe == ()
    assert report.passed


# AC12 -----------------------------------------------------------------------------------
def test_ac12_bulk_handles_10000_payouts_with_aggregated_counts(
    registry: Registry, policy: Policy, clock: Clock
) -> None:
    rows = generate_payouts(registry, 10_000)
    payouts = [Payout.model_validate(r | {"bulk": True}) for r in rows]
    log = EvidenceLog()
    summary = run_bulk(payouts, build_adapters(registry), policy, log, clock=clock)
    assert summary.total == 10_000 == len(log)
    assert sum(summary.by_outcome.values()) == 10_000
    assert sum(summary.by_verdict.values()) == 10_000
    assert set(summary.by_system_outcome) == {s.value for s in System}
    assert all(v > 0 for v in summary.by_outcome.values())
    assert log.verify() == []
