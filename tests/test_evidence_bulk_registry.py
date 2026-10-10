from __future__ import annotations

from pathlib import Path

import pytest

from payee_verdict.adapters import Adapter
from payee_verdict.bulk import (
    BulkInputError,
    generate_payouts,
    read_payouts,
    render_summary,
    run_bulk,
)
from payee_verdict.corpus import CorpusError, load_corpus, render_markdown, run_corpus
from payee_verdict.engine import Clock, EngineError, verify_payout
from payee_verdict.evidence import EvidenceLog, render_diff, replay
from payee_verdict.models import Payout, System
from payee_verdict.policy import Policy
from payee_verdict.registry import Registry, RegistryError, load_registry
from tests.conftest import CORPUS, payout


def test_generated_payouts_are_deterministic(registry: Registry) -> None:
    assert generate_payouts(registry, 50, seed=3) == generate_payouts(registry, 50, seed=3)
    assert generate_payouts(registry, 50, seed=3) != generate_payouts(registry, 50, seed=4)


def test_read_payouts_errors(tmp_path: Path) -> None:
    bad_json = tmp_path / "x.jsonl"
    bad_json.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(BulkInputError, match="invalid JSON"):
        read_payouts(bad_json)
    bad_row = tmp_path / "x.csv"
    bad_row.write_text("payout_id,system\nP1,MARS_PAY\n", encoding="utf-8")
    with pytest.raises(BulkInputError, match="row 1"):
        read_payouts(bad_row)


def test_bulk_flag_is_forced(tmp_path: Path, registry: Registry) -> None:
    from payee_verdict.bulk import write_csv

    path = tmp_path / "p.csv"
    write_csv(generate_payouts(registry, 5), path)
    assert all(p.bulk for p in read_payouts(path))
    assert not any(p.bulk for p in read_payouts(path, bulk=False))


def test_summary_render(
    registry: Registry, policy: Policy, clock: Clock, adapters: dict[System, Adapter]
) -> None:
    payouts = [Payout.model_validate(r | {"bulk": True}) for r in generate_payouts(registry, 200)]
    text = render_summary(run_bulk(payouts, adapters, policy, EvidenceLog(), clock=clock))
    assert text.startswith("payouts: 200")
    assert "TOTAL" in text and "fallbacks applied:" in text


def test_engine_without_adapter(policy: Policy, clock: Clock) -> None:
    with pytest.raises(EngineError, match="no adapter configured for BR_PIX"):
        verify_payout(payout(System.BR_PIX, "k", "n"), {}, policy, EvidenceLog(), clock=clock)


def test_engine_uses_wall_clock_by_default(policy: Policy, adapters: dict[System, Adapter]) -> None:
    v = verify_payout(payout(System.BR_PIX, "k", "n"), adapters, policy, EvidenceLog())
    assert v.record.at.tzinfo is not None


def test_diff_render_limits_and_empty_log(
    registry: Registry,
    policy: Policy,
    strict_policy: Policy,
    clock: Clock,
    adapters: dict[System, Adapter],
) -> None:
    payouts = [Payout.model_validate(r | {"bulk": True}) for r in generate_payouts(registry, 300)]
    log = EvidenceLog()
    run_bulk(payouts, adapters, policy, log, clock=clock)
    text = render_diff(replay(log, strict_policy), limit=2)
    assert "more." in text
    empty = render_diff(replay(EvidenceLog(), strict_policy))
    assert "(empty log)" in empty
    assert "Verdicts changed: 0" in empty


def test_evidence_without_match_has_no_normalisation(
    policy: Policy, clock: Clock, adapters: dict[System, Adapter]
) -> None:
    log = EvidenceLog()
    verify_payout(
        payout(System.BR_PIX, "nobody@pix.example", "n"), adapters, policy, log, clock=clock
    )
    assert log.records[0].normalisation == {}


def test_registry_errors(tmp_path: Path) -> None:
    dup = tmp_path / "r.yaml"
    dup.write_text(
        "br_pix:\n"
        "  - {key: a@pix.example, key_type: EMAIL, name: A, type: PERSON}\n"
        "  - {key: a@pix.example, key_type: EMAIL, name: B, type: PERSON}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(dup)
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "missing.yaml")


def test_corpus_errors_and_markdown(tmp_path: Path) -> None:
    dup = tmp_path / "c.yaml"
    case = (
        '{id: A, category: c, payee_type: PERSON, requested: "x", actual: "x", '
        'expected: MATCH, rationale: "same"}'
    )
    dup.write_text(
        f"version: 1\nthreshold: 0.5\ncases:\n  - {case}\n  - {case}\n", encoding="utf-8"
    )
    with pytest.raises(CorpusError, match="duplicate case ids"):
        load_corpus(dup)
    single = tmp_path / "s.yaml"
    single.write_text(f"version: 1\nthreshold: 0.5\ncases:\n  - {case}\n", encoding="utf-8")
    corpus, digest = load_corpus(single)
    md = render_markdown(run_corpus(corpus, digest))
    assert "## Misses\n\nNone." in md
    full, d = load_corpus(CORPUS)
    assert "| KG-01 |" in render_markdown(run_corpus(full, d))
