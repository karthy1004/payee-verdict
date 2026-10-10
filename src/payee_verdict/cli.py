"""Command line: `python -m payee_verdict verify | bulk | corpus | replay | diff-policy | demo`."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from payee_verdict import __version__
from payee_verdict.adapters import build_adapters
from payee_verdict.bulk import generate_payouts, read_payouts, render_summary, run_bulk, write_csv
from payee_verdict.corpus import (
    load_corpus,
    render_markdown,
    render_text,
    run_corpus,
)
from payee_verdict.corpus import (
    to_json as corpus_json,
)
from payee_verdict.engine import Clock, Verification, verify_payout
from payee_verdict.evidence import EvidenceLog, render_diff, replay
from payee_verdict.models import PayeeType, Payout, System
from payee_verdict.policy import load_policy
from payee_verdict.registry import load_registry

DEFAULT_POLICY = Path("policies/default-v1.yaml")
DEFAULT_STRICT_POLICY = Path("policies/strict-v2.yaml")
DEFAULT_REGISTRY = Path("registry/synthetic-registry.yaml")
DEFAULT_CORPUS = Path("corpus/names-v1.yaml")
DEFAULT_RESULTS = Path("corpus/results")

DEFAULT_CURRENCY = {
    System.EU_VOP: "EUR",
    System.UK_COP: "GBP",
    System.IN_NAME_LOOKUP: "INR",
    System.BR_PIX: "BRL",
}


def fixed_clock(start: datetime, step: timedelta = timedelta(seconds=1)) -> Clock:
    """A deterministic clock for demos and tests: start, start+step, ..."""
    state = {"t": start - step}

    def tick() -> datetime:
        state["t"] = state["t"] + step
        return state["t"]

    return tick


def _load_log(path: Path) -> EvidenceLog:
    return (
        EvidenceLog.from_jsonl(path.read_text(encoding="utf-8")) if path.exists() else EvidenceLog()
    )


def _verification_json(v: Verification) -> str:
    return json.dumps(
        {
            "payout_id": v.payout.payout_id,
            "system": v.payout.system.value,
            "outcome": v.check.outcome.value,
            "rule_id": v.check.rule_id,
            "reason_code": v.check.reason_code.value if v.check.reason_code else None,
            "account_holder_name": v.check.account_holder_name,
            "verdict": v.decision.verdict.value,
            "policy": f"{v.decision.policy_id}@{v.decision.policy_version}",
            "policy_rule_id": v.decision.policy_rule_id,
            "amount_band": v.decision.amount_band.value,
            "fallback_applied": v.decision.fallback_applied,
            "payer_message": v.payer_message,
            "evidence_hash": v.record.hash,
        },
        indent=2,
        ensure_ascii=False,
    )


def _verification_text(v: Verification) -> str:
    lines = [
        f"{v.payout.system.value}: {v.check.outcome.value} ({v.check.rule_id})"
        + (f" reason={v.check.reason_code.value}" if v.check.reason_code else ""),
        f"verdict: {v.decision.verdict.value} by {v.decision.policy_rule_id} "
        f"[{v.decision.policy_id}@{v.decision.policy_version}, "
        f"band {v.decision.amount_band.value}]",
    ]
    if v.decision.fallback_applied:
        lines.append(f"fallback applied: {v.decision.fallback_applied}")
    if v.check.match and v.check.match.requested:
        changed = [s for s in v.check.match.requested.trace if s.changed]
        lines.append("normalisation of the entered name:")
        lines += [f"  {s.step:<17} {s.before!r} -> {s.after!r}" for s in changed]
    lines.append(f"payer sees: {v.payer_message}")
    lines.append(f"evidence: record #{v.record.seq} {v.record.hash[:16]}")
    return "\n".join(lines)


def cmd_verify(args: argparse.Namespace) -> int:
    system = System(args.system)
    payout = Payout(
        payout_id=args.payout_id,
        system=system,
        account=args.account,
        payee_name=args.name,
        payee_type=PayeeType(args.type),
        amount=Decimal(args.amount),
        currency=args.currency or DEFAULT_CURRENCY[system],
        bulk=args.bulk,
        payee_id_code=args.id_code,
    )
    adapters = build_adapters(load_registry(args.registry), india_available=not args.india_down)
    policy = load_policy(args.policy)
    log = _load_log(Path(args.evidence)) if args.evidence else EvidenceLog()
    v = verify_payout(payout, adapters, policy, log)
    if args.evidence:
        Path(args.evidence).write_text(log.to_jsonl(), encoding="utf-8")
    print(_verification_json(v) if args.format == "json" else _verification_text(v))
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    rows = generate_payouts(load_registry(args.registry), args.count, seed=args.seed)
    write_csv(rows, args.out)
    print(f"wrote {len(rows)} synthetic payouts to {args.out}")
    return 0


def cmd_bulk(args: argparse.Namespace) -> int:
    adapters = build_adapters(load_registry(args.registry), india_available=not args.india_down)
    policy = load_policy(args.policy)
    started = time.perf_counter()
    payouts = read_payouts(args.input)
    log = EvidenceLog()
    summary = run_bulk(payouts, adapters, policy, log)
    if args.evidence:
        Path(args.evidence).write_text(log.to_jsonl(), encoding="utf-8")
    elapsed = time.perf_counter() - started
    print(render_summary(summary), end="")
    problems = log.verify()
    print(f"evidence: {len(log)} records, chain {'intact' if not problems else 'BROKEN'}")
    print(f"policy: {policy.id}@{policy.version}")
    print(
        f"wall time: {elapsed:.2f} s (read + verify + decide + evidence"
        f"{' + write' if args.evidence else ''})"
    )
    if args.summary_json:
        Path(args.summary_json).write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return 0


def cmd_corpus(args: argparse.Namespace) -> int:
    corpus, digest = load_corpus(args.corpus)
    report = run_corpus(corpus, digest)
    print(render_text(report), end="")
    if args.write:
        out = Path(args.results_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "latest.md").write_text(render_markdown(report), encoding="utf-8")
        (out / "latest.json").write_text(corpus_json(report), encoding="utf-8")
        print(f"wrote {out / 'latest.md'} and {out / 'latest.json'}")
    return 0 if report.passed else 1


def cmd_replay(args: argparse.Namespace) -> int:
    log = _load_log(Path(args.evidence))
    policy = load_policy(args.policy)
    report = replay(log, policy)
    target = f"{policy.id}@{policy.version}"
    print(f"replayed {len(report.items)} records under {target}")
    print(f"recorded under: {', '.join(report.recorded_policies) or '(none)'}")
    print(f"chain: {'intact' if not report.chain_problems else 'BROKEN'}")
    for p in report.chain_problems:
        print(f"  {p}")
    print(f"identical verdicts: {len(report.items) - len(report.changed)}/{len(report.items)}")
    same_policy = report.recorded_policies == (target,)
    if report.chain_problems or (same_policy and report.changed):
        return 1
    return 0


def cmd_diff_policy(args: argparse.Namespace) -> int:
    log = _load_log(Path(args.evidence))
    report = replay(log, load_policy(args.new_policy))
    text = render_diff(report)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")
    return 1 if report.chain_problems else 0


DEMO_CASES: tuple[tuple[str, str, str, str, str, str, str | None], ...] = (
    # (label, system, account, name entered, type, amount, id code)
    ("umlaut vs ue", "EU_VOP", "DE00TEST0000000001", "MUELLER GMBH", "ENTITY", "2500", None),
    (
        "Limited vs Ltd",
        "EU_VOP",
        "IE00TEST0000000002",
        "Blue Fjord Bakery Limited",
        "ENTITY",
        "480",
        None,
    ),
    ("GmbH vs AG", "EU_VOP", "DE00TEST0000000011", "Grünfeld Solar GmbH", "ENTITY", "900", None),
    (
        "LEI instead of name",
        "EU_VOP",
        "IT00TEST0000000008",
        "",
        "ENTITY",
        "15000",
        "9999TESTPAYEE0000874",
    ),
    (
        "slow responder",
        "EU_VOP",
        "AT00TEST0000000007",
        "Kranzhofer Holzbau GmbH & Co. KG",
        "ENTITY",
        "300",
        None,
    ),
    ("name reversed", "UK_COP", "99-00-01:00000001", "Okonkwo-Hale Adaeze", "PERSON", "250", None),
    (
        "wrong account type",
        "UK_COP",
        "99-00-05:00000005",
        "Tobias Wrenfield",
        "PERSON",
        "120",
        None,
    ),
    (
        "different company",
        "UK_COP",
        "99-00-02:00000002",
        "Harrowgate Roofing Ltd",
        "ENTITY",
        "4000",
        None,
    ),
    (
        "lookup name, typo",
        "IN_NAME_LOOKUP",
        "TEST0000001:000000000001",
        "Priyadarshini Venkatraman",
        "PERSON",
        "75000",
        None,
    ),
    (
        "lookup unavailable",
        "IN_NAME_LOOKUP",
        "TEST0000004:000000000004",
        "Sahyadri Loom Works Pvt Ltd",
        "ENTITY",
        "250000",
        None,
    ),
    (
        "flagged PIX key",
        "BR_PIX",
        "00000000-0000-4000-8000-000000000002",
        "Caio Venâncio Delamare",
        "PERSON",
        "800",
        None,
    ),
    (
        "PIX, accent dropped",
        "BR_PIX",
        "pagamentos@cafe-serrano.example",
        "Cafe Serrano Torrefacao Ltda",
        "ENTITY",
        "3200",
        None,
    ),
)


def cmd_demo(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    registry = load_registry(args.registry)
    policy = load_policy(args.policy)
    adapters = build_adapters(registry)
    clock = fixed_clock(datetime(2026, 10, 10, 9, 0, tzinfo=UTC))
    log = EvidenceLog()
    print(f"payee-verdict {__version__} demo · policy {policy.id}@{policy.version}")
    print("Synthetic registry and simulated systems; no network calls.\n")
    print(f"{'#':>2} {'case':<20} {'system':<15} {'outcome':<13} {'rule':<26} verdict")
    results: list[Verification] = []
    for i, (label, system, account, name, ptype, amount, id_code) in enumerate(DEMO_CASES, 1):
        payout = Payout(
            payout_id=f"DEMO-{i:02d}",
            system=System(system),
            account=account,
            payee_name=name,
            payee_type=PayeeType(ptype),
            amount=Decimal(amount),
            currency=DEFAULT_CURRENCY[System(system)],
            payee_id_code=id_code,
        )
        v = verify_payout(payout, adapters, policy, log, clock=clock)
        results.append(v)
        print(
            f"{i:>2} {label:<20} {system:<15} {v.check.outcome.value:<13} "
            f"{v.check.rule_id:<26} {v.decision.verdict.value}"
        )

    first = results[0]
    assert first.check.match is not None and first.check.match.actual is not None
    print(f"\nTrace for #1, registered name {first.check.match.actual.original!r}:")
    for s in first.check.match.actual.trace:
        if s.changed:
            print(f"  {s.step:<17} {s.before!r} -> {s.after!r}")
    print(f"  both sides normalise to {first.check.match.actual.key!r}")

    print("\nWhat the payer sees:")
    for idx in (5, 7, 10):
        v = results[idx]
        print(f"  #{idx + 1} {v.check.outcome.value}: {v.payer_message}")
    fb = results[9]
    print(f"\n#10 fallback recorded in evidence: {fb.record.fallback_applied}")

    print(f"\nEvidence log: {len(log)} records, head {log.head[:16]}")
    print(f"  chain check: {'intact' if not log.verify() else log.verify()}")
    tampered = EvidenceLog.from_jsonl(
        log.to_jsonl().replace('"verdict":"HOLD"', '"verdict":"PROCEED"', 1)
    )
    print(f"  after editing one HOLD to PROCEED: {tampered.verify()[0]}")

    same = replay(log, policy)
    print(
        f"\nReplay under {policy.id}@{policy.version}: "
        f"{len(same.items) - len(same.changed)}/{len(same.items)} verdicts identical"
    )
    strict = load_policy(args.diff_policy)
    diff = replay(log, strict)
    print(f"Diff against {strict.id}@{strict.version}: {len(diff.changed)} verdict(s) change")
    for item in diff.changed:
        print(
            f"  {item.payout_id}: {item.recorded_verdict.value} -> {item.replayed_verdict.value} "
            f"({item.replayed_rule})"
        )
    print(f"\ndemo finished in {time.perf_counter() - started:.2f} s")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="payee-verdict",
        description="Explainable payee-name verification across simulated payment systems.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--policy", default=str(DEFAULT_POLICY), help="policy YAML")
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="synthetic registry YAML")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify", help="verify one payout")
    v.add_argument("--system", required=True, choices=[s.value for s in System])
    v.add_argument("--account", required=True)
    v.add_argument("--name", default="")
    v.add_argument("--type", required=True, choices=[t.value for t in PayeeType])
    v.add_argument("--amount", required=True)
    v.add_argument("--currency")
    v.add_argument("--id-code")
    v.add_argument("--payout-id", default="CLI-1")
    v.add_argument("--bulk", action="store_true")
    v.add_argument("--india-down", action="store_true", help="simulate an India lookup outage")
    v.add_argument("--evidence", help="JSONL evidence log to append to")
    v.add_argument("--format", choices=["text", "json"], default="text")
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("bulk", help="verify a CSV or JSONL file of payouts")
    b.add_argument("input")
    b.add_argument("--evidence", help="write the evidence log here (JSONL)")
    b.add_argument("--summary-json", help="write aggregated counts here")
    b.add_argument("--india-down", action="store_true")
    b.set_defaults(func=cmd_bulk)

    g = sub.add_parser("generate", help="write a synthetic payout CSV for load tests")
    g.add_argument("--count", type=int, default=10_000)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--out", required=True)
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("corpus", help="run the conformance corpus")
    c.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    c.add_argument("--write", action="store_true", help="write results to --results-dir")
    c.add_argument("--results-dir", default=str(DEFAULT_RESULTS))
    c.set_defaults(func=cmd_corpus)

    r = sub.add_parser("replay", help="re-decide an evidence log under --policy")
    r.add_argument("evidence")
    r.set_defaults(func=cmd_replay)

    d = sub.add_parser("diff-policy", help="which verdicts change under a new policy")
    d.add_argument("evidence")
    d.add_argument("--new-policy", required=True)
    d.add_argument("--out")
    d.set_defaults(func=cmd_diff_policy)

    m = sub.add_parser("demo", help="walk through the interesting cases")
    m.add_argument("--diff-policy", default=str(DEFAULT_STRICT_POLICY))
    m.set_defaults(func=cmd_demo)
    return p


def _utf8_stdout() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no branch
        with contextlib.suppress(ValueError, OSError):
            sys.stdout.reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_stdout()
    args = _parser().parse_args(argv)
    code: int = args.func(args)
    return code
