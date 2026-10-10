from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from payee_verdict.cli import main
from tests.conftest import CORPUS, POLICY, REGISTRY, ROOT, STRICT_POLICY

BASE = ["--policy", str(POLICY), "--registry", str(REGISTRY)]


def test_verify_text_shows_trace_and_message(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            *BASE,
            "verify",
            "--system",
            "EU_VOP",
            "--account",
            "DE00TEST0000000001",
            "--name",
            "Müller GmbH",
            "--type",
            "ENTITY",
            "--amount",
            "500",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "EU_VOP: MATCH (EXACT_NORMALISED)" in out
    assert "verdict: PROCEED by R02-match-proceed" in out
    assert "transliterate     'Müller GmbH' -> 'Mueller GmbH'" in out


def test_verify_json_appends_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ev = tmp_path / "ev.jsonl"
    args = [
        *BASE,
        "verify",
        "--system",
        "IN_NAME_LOOKUP",
        "--account",
        "TEST0000001:000000000001",
        "--name",
        "Priyadarsini Venkatraman",
        "--type",
        "PERSON",
        "--amount",
        "5000",
        "--format",
        "json",
        "--evidence",
        str(ev),
        "--india-down",
    ]
    assert main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["reason_code"] == "LOOKUP_UNAVAILABLE"
    assert data["fallback_applied"] == "IN_NAME_LOOKUP/LOOKUP_UNAVAILABLE -> WARN_CONFIRM"
    assert main(args) == 0
    capsys.readouterr()
    lines = ev.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["prev_hash"] == json.loads(lines[0])["hash"]


def test_verify_text_shows_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            *BASE,
            "verify",
            "--system",
            "IN_NAME_LOOKUP",
            "--account",
            "TEST0000004:000000000004",
            "--name",
            "Sahyadri Loom Works",
            "--type",
            "ENTITY",
            "--amount",
            "100",
        ]
    )
    out = capsys.readouterr().out
    assert "fallback applied: IN_NAME_LOOKUP/LOOKUP_UNAVAILABLE -> WARN_CONFIRM" in out
    assert "reason=LOOKUP_UNAVAILABLE" in out


def test_generate_bulk_replay_and_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    csv_path = tmp_path / "payouts.csv"
    ev = tmp_path / "ev.jsonl"
    summary = tmp_path / "summary.json"
    diff = tmp_path / "diff.md"
    assert main([*BASE, "generate", "--count", "300", "--out", str(csv_path)]) == 0
    assert (
        main([*BASE, "bulk", str(csv_path), "--evidence", str(ev), "--summary-json", str(summary)])
        == 0
    )
    out = capsys.readouterr().out
    assert "payouts: 300" in out
    assert "chain intact" in out
    assert json.loads(summary.read_text(encoding="utf-8"))["total"] == 300

    assert main([*BASE, "replay", str(ev)]) == 0
    out = capsys.readouterr().out
    assert "identical verdicts: 300/300" in out

    assert (
        main(
            [*BASE, "diff-policy", str(ev), "--new-policy", str(STRICT_POLICY), "--out", str(diff)]
        )
        == 0
    )
    text = diff.read_text(encoding="utf-8")
    assert "Records replayed: 300" in text
    assert "| WARN_CONFIRM | HOLD |" in text

    # replaying under a different policy is a diff, not a failure
    assert (
        main(["--policy", str(STRICT_POLICY), "--registry", str(REGISTRY), "replay", str(ev)]) == 0
    )

    # a tampered log makes replay and diff-policy fail
    lines = ev.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[5])
    rec["verdict"] = "PROCEED" if rec["verdict"] != "PROCEED" else "HOLD"
    lines[5] = json.dumps(rec, ensure_ascii=False)
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    capsys.readouterr()
    assert main([*BASE, "replay", str(ev)]) == 1
    assert "record 5: content does not match its hash" in capsys.readouterr().out
    assert main([*BASE, "diff-policy", str(ev), "--new-policy", str(STRICT_POLICY)]) == 1


def test_bulk_jsonl_without_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "p.jsonl"
    src.write_text(
        json.dumps(
            {
                "payout_id": "J1",
                "system": "BR_PIX",
                "account": "loja.aurora@pix.example",
                "payee_name": "Aurora Doces Artesanais",
                "payee_type": "ENTITY",
                "amount": "60000",
                "currency": "BRL",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert main([*BASE, "bulk", str(src), "--india-down"]) == 0
    out = capsys.readouterr().out
    assert "payouts: 1" in out
    assert "verdicts: PROCEED 1" in out


def test_corpus_command_writes_results(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["corpus", "--corpus", str(CORPUS), "--write", "--results-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "result: PASS" in out
    md = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "## Per category" in md
    data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert data["passed"] is True
    assert data["total"] >= 150


def test_corpus_command_fails_below_threshold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\nthreshold: 0.9\ncases:\n"
        '  - {id: X1, category: c, payee_type: PERSON, requested: "Ana Lind", '
        'actual: "Ana Lind", expected: NO_MATCH, rationale: "deliberately wrong label"}\n',
        encoding="utf-8",
    )
    assert main(["corpus", "--corpus", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "unsafe upgrades (false MATCH): 1" in out
    assert "result: FAIL" in out


def test_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*BASE, "demo", "--diff-policy", str(STRICT_POLICY)]) == 0
    out = capsys.readouterr().out
    assert "chain check: intact" in out
    assert "12/12 verdicts identical" in out
    assert "SYSTEM_KEY_FLAGGED" in out
    assert "content does not match its hash" in out


def test_committed_corpus_results_are_current(tmp_path: Path) -> None:
    main(["corpus", "--corpus", str(CORPUS), "--write", "--results-dir", str(tmp_path)])
    committed = ROOT / "corpus" / "results" / "latest.json"
    assert committed.read_text(encoding="utf-8") == (tmp_path / "latest.json").read_text(
        encoding="utf-8"
    ), "re-run `python -m payee_verdict corpus --write` and commit the results"


def test_module_entry_point() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "payee_verdict", "--version"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert proc.stdout.strip() == "payee-verdict 0.1.0"
