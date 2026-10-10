"""Conformance corpus: labelled name pairs, run through the reference matcher.

Two gates: overall accuracy at or above the corpus threshold, and zero *unsafe upgrades*
(MATCH predicted where the label says anything else). An unsafe upgrade is the error
that lets money go to the wrong payee without a warning, so it is gated separately.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from payee_verdict.evidence import sha256_hex
from payee_verdict.matcher import MATCHER_VERSION, match_names
from payee_verdict.models import Outcome, PayeeType


class CorpusError(ValueError):
    pass


class CorpusCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    category: str
    payee_type: PayeeType
    requested: str
    actual: str
    expected: Outcome
    rationale: str = Field(min_length=3)


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    threshold: float = Field(gt=0, le=1)
    cases: tuple[CorpusCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Corpus:
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case ids")
        return self


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    expected: Outcome
    predicted: Outcome
    rule_id: str
    rationale: str

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted

    @property
    def unsafe(self) -> bool:
        return self.predicted is Outcome.MATCH and self.expected is not Outcome.MATCH


class CategoryScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    total: int
    correct: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total


class CorpusReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    corpus_version: int
    corpus_sha256: str
    matcher_version: str
    threshold: float
    results: tuple[CaseResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(r.correct for r in self.results)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total

    @property
    def unsafe(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.unsafe)

    @property
    def misses(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if not r.correct)

    @property
    def passed(self) -> bool:
        return self.accuracy >= self.threshold and not self.unsafe

    def by_category(self) -> list[CategoryScore]:
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in self.results:
            totals[r.category][0] += 1
            totals[r.category][1] += int(r.correct)
        return [CategoryScore(category=c, total=t, correct=k) for c, (t, k) in totals.items()]

    def confusion(self) -> dict[Outcome, dict[Outcome, int]]:
        grid = {e: dict.fromkeys(Outcome, 0) for e in Outcome}
        for r in self.results:
            grid[r.expected][r.predicted] += 1
        return grid


def load_corpus(path: str | Path) -> tuple[Corpus, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
        return Corpus.model_validate(yaml.safe_load(text)), sha256_hex(text)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CorpusError(f"cannot load corpus {path}: {exc}") from exc


def run_corpus(corpus: Corpus, corpus_sha256: str = "") -> CorpusReport:
    results = []
    for case in corpus.cases:
        m = match_names(case.requested, case.actual, case.payee_type)
        results.append(
            CaseResult(
                id=case.id,
                category=case.category,
                expected=case.expected,
                predicted=m.outcome,
                rule_id=m.rule_id,
                rationale=case.rationale,
            )
        )
    return CorpusReport(
        corpus_version=corpus.version,
        corpus_sha256=corpus_sha256,
        matcher_version=MATCHER_VERSION,
        threshold=corpus.threshold,
        results=tuple(results),
    )


def render_text(report: CorpusReport) -> str:
    lines = [f"{'category':<22}{'cases':>6}{'correct':>9}{'accuracy':>10}"]
    for s in report.by_category():
        lines.append(f"{s.category:<22}{s.total:>6}{s.correct:>9}{s.accuracy:>10.1%}")
    lines.append(f"{'OVERALL':<22}{report.total:>6}{report.correct:>9}{report.accuracy:>10.1%}")
    lines.append("")
    lines.append(
        f"threshold {report.threshold:.0%}; unsafe upgrades (false MATCH): {len(report.unsafe)}"
    )
    lines.append(f"result: {'PASS' if report.passed else 'FAIL'}")
    if report.misses:
        lines.append("")
        lines.append("misses:")
        for r in report.misses:
            lines.append(
                f"  {r.id:<7} expected {r.expected.value:<12} got {r.predicted.value:<12} "
                f"({r.rule_id})"
            )
    return "\n".join(lines) + "\n"


def render_markdown(report: CorpusReport) -> str:
    lines = [
        "# Conformance corpus results",
        "",
        f"- Corpus version {report.corpus_version}, sha256 `{report.corpus_sha256[:16]}…`",
        f"- Matcher version {report.matcher_version}",
        f"- Overall: **{report.correct}/{report.total} = {report.accuracy:.1%}** "
        f"(threshold {report.threshold:.0%})",
        f"- Unsafe upgrades (MATCH where the label is not MATCH): **{len(report.unsafe)}**",
        f"- Result: **{'PASS' if report.passed else 'FAIL'}**",
        "",
        "Generated by `python -m payee_verdict corpus --write`. Do not edit by hand.",
        "",
        "## Per category",
        "",
        "| Category | Cases | Correct | Accuracy |",
        "|---|---:|---:|---:|",
    ]
    for s in report.by_category():
        lines.append(f"| {s.category} | {s.total} | {s.correct} | {s.accuracy:.1%} |")
    lines += ["", "## Confusion (rows = expected, columns = predicted)", ""]
    outcomes = list(Outcome)
    lines.append("| expected \\ predicted | " + " | ".join(o.value for o in outcomes) + " |")
    lines.append("|---|" + "---:|" * len(outcomes))
    for e, row in report.confusion().items():
        lines.append(f"| {e.value} | " + " | ".join(str(row[o]) for o in outcomes) + " |")
    lines += ["", "## Misses", ""]
    if not report.misses:
        lines.append("None.")
    else:
        lines += [
            "| Case | Category | Expected | Got | Rule | Why the label |",
            "|---|---|---|---|---|---|",
        ]
        for r in report.misses:
            lines.append(
                f"| {r.id} | {r.category} | {r.expected.value} | {r.predicted.value} "
                f"| `{r.rule_id}` | {r.rationale} |"
            )
    return "\n".join(lines) + "\n"


def to_json(report: CorpusReport) -> str:
    data = {
        "corpus_version": report.corpus_version,
        "corpus_sha256": report.corpus_sha256,
        "matcher_version": report.matcher_version,
        "threshold": report.threshold,
        "total": report.total,
        "correct": report.correct,
        "accuracy": round(report.accuracy, 4),
        "unsafe_upgrades": len(report.unsafe),
        "passed": report.passed,
        "categories": {
            s.category: {"total": s.total, "correct": s.correct, "accuracy": round(s.accuracy, 4)}
            for s in report.by_category()
        },
        "results": [
            {
                "id": r.id,
                "category": r.category,
                "expected": r.expected.value,
                "predicted": r.predicted.value,
                "rule_id": r.rule_id,
            }
            for r in report.results
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
