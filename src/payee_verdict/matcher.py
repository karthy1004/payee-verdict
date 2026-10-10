"""Reference rule-based matcher, as a payee's bank (the responder) would run it.

Rules are evaluated in a fixed order and the first that applies decides. Every result
carries the ID of that rule. People and legal entities have separate rule lists because
the failure modes differ: a reordered person name is a different signal from a
reordered company name, and legal forms only exist for entities.

Design choices that matter:

* A transposed person name is CLOSE_MATCH (`TOKEN_REORDER`), never MATCH. "Hale Adaeze"
  could be a different person from "Adaeze Hale", so the payer should see the real name.
* A legal form that is merely spelled differently ("Ltd" / "Limited") or missing on one
  side is MATCH (`LEGAL_FORM_EQUIV`). Two *different* legal forms ("GmbH" / "AG") are
  CLOSE_MATCH (`LEGAL_FORM_CONFLICT`), because a GmbH and an AG with the same core name
  can be two companies in one group.
* An ID-code check (LEI or VAT style) only ever returns MATCH or NO_MATCH.

These are reference heuristics, not any scheme's matching rules.
"""

from __future__ import annotations

from payee_verdict.models import MatchResult, NormalisedName, Outcome, PayeeType
from payee_verdict.normalize import normalize, normalize_identifier

CLOSE_RATIO = 0.85
TRUNCATION_MIN_CHARS = 10
TRUNCATION_MIN_SHARE = 0.5

MATCHER_VERSION = "0.1.0"


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """1 - edit distance / longer length, rounded to 4 places for stable evidence."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return round(1 - levenshtein(a, b) / longest, 4)


def _result(
    outcome: Outcome,
    rule_id: str,
    sim: float,
    explanation: str,
    req: NormalisedName,
    act: NormalisedName,
) -> MatchResult:
    return MatchResult(
        outcome=outcome,
        rule_id=rule_id,
        similarity=sim,
        explanation=explanation,
        requested=req,
        actual=act,
    )


def _is_truncation(a: str, b: str) -> bool:
    short, long_ = sorted((a, b), key=len)
    return (
        short != long_
        and long_.startswith(short)
        and len(short) >= TRUNCATION_MIN_CHARS
        and len(short) / len(long_) >= TRUNCATION_MIN_SHARE
    )


def _tokens_compatible(x: str, y: str) -> str | None:
    if x == y:
        return "eq"
    if len(x) == 1 and y.startswith(x):
        return "init"
    if len(y) == 1 and x.startswith(y):
        return "init"
    return None


def _person_alignment(a: tuple[str, ...], b: tuple[str, ...]) -> str | None:
    """Align the shorter name into the longer, in order.

    Requires the surname (last token) to be equal and the first tokens to be compatible.
    Returns "INITIALS", "MISSING_MIDDLE_NAME" or None.
    """
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 2 or short[-1] != long_[-1]:
        return None
    kinds: list[str] = []
    first = _tokens_compatible(short[0], long_[0])
    if first is None:
        return None
    kinds.append(first)
    j = 1
    for tok in short[1:-1]:
        while j < len(long_) - 1 and _tokens_compatible(tok, long_[j]) is None:
            j += 1
        if j >= len(long_) - 1:
            return None
        kind = _tokens_compatible(tok, long_[j])
        assert kind is not None
        kinds.append(kind)
        j += 1
    if "init" in kinds:
        return "INITIALS"
    if len(long_) > len(short):
        return "MISSING_MIDDLE_NAME"
    return None  # pragma: no cover - equal token lists are caught by EXACT_NORMALISED


def _overlap_or_none(req: NormalisedName, act: NormalisedName, sim: float) -> MatchResult:
    common = set(req.core_tokens) & set(act.core_tokens)
    if common:
        return _result(
            Outcome.NO_MATCH,
            "PARTIAL_TOKEN_OVERLAP",
            sim,
            f"only {len(common)} token(s) in common ({', '.join(sorted(common))}); "
            "not enough to identify the same payee",
            req,
            act,
        )
    return _result(Outcome.NO_MATCH, "NO_TOKEN_OVERLAP", sim, "no tokens in common", req, act)


def _match_person(req: NormalisedName, act: NormalisedName) -> MatchResult:
    sim = similarity(req.core, act.core)
    if req.key == act.key:
        return _result(
            Outcome.MATCH, "EXACT_NORMALISED", sim, "identical after normalisation", req, act
        )
    if req.folded_core == act.folded_core:
        return _result(
            Outcome.MATCH,
            "TRANSLITERATION_EQUIV",
            sim,
            "same letters once accents are folded (e.g. ü typed as u)",
            req,
            act,
        )
    if req.core.replace(" ", "") == act.core.replace(" ", ""):
        return _result(
            Outcome.MATCH, "SPACING_EQUIV", sim, "differs only in spacing or hyphens", req, act
        )
    if sorted(req.core_tokens) == sorted(act.core_tokens):
        return _result(
            Outcome.CLOSE_MATCH,
            "TOKEN_REORDER",
            sim,
            "same name parts in a different order; could be a different person",
            req,
            act,
        )
    aligned = _person_alignment(req.core_tokens, act.core_tokens)
    if aligned == "INITIALS":
        return _result(
            Outcome.CLOSE_MATCH,
            "INITIALS",
            sim,
            "surname equal; given name(s) given as initials",
            req,
            act,
        )
    if aligned == "MISSING_MIDDLE_NAME":
        return _result(
            Outcome.CLOSE_MATCH,
            "MISSING_MIDDLE_NAME",
            sim,
            "first and last names equal; a middle name is missing on one side",
            req,
            act,
        )
    if req.core_tokens[0] == act.core_tokens[0] and _is_truncation(req.core, act.core):
        return _result(
            Outcome.CLOSE_MATCH, "TRUNCATION", sim, "one name is a cut-off prefix", req, act
        )
    sorted_sim = similarity(" ".join(sorted(req.core_tokens)), " ".join(sorted(act.core_tokens)))
    if max(sim, sorted_sim) >= CLOSE_RATIO:
        return _result(
            Outcome.CLOSE_MATCH,
            "EDIT_DISTANCE_CLOSE",
            max(sim, sorted_sim),
            f"similarity {max(sim, sorted_sim):.2f} >= {CLOSE_RATIO}; likely a typo",
            req,
            act,
        )
    return _overlap_or_none(req, act, sim)


def _match_entity(req: NormalisedName, act: NormalisedName) -> MatchResult:
    sim = similarity(req.core, act.core)
    if req.key == act.key:
        return _result(
            Outcome.MATCH, "EXACT_NORMALISED", sim, "identical after normalisation", req, act
        )
    conflict = (
        req.legal_form is not None
        and act.legal_form is not None
        and req.legal_form != act.legal_form
    )
    same_core = req.core == act.core
    folded = req.folded_core == act.folded_core
    spaced = req.core.replace(" ", "") == act.core.replace(" ", "")
    if same_core or folded or spaced:
        if conflict:
            return _result(
                Outcome.CLOSE_MATCH,
                "LEGAL_FORM_CONFLICT",
                sim,
                f"same name but different legal forms ({req.legal_form} vs {act.legal_form}); "
                "may be a different company in the same group",
                req,
                act,
            )
        if same_core:
            return _result(
                Outcome.MATCH,
                "LEGAL_FORM_EQUIV",
                sim,
                "same name; legal form spelled differently or omitted "
                f"({req.legal_form_surface or '-'} vs {act.legal_form_surface or '-'})",
                req,
                act,
            )
        if folded:
            return _result(
                Outcome.MATCH,
                "TRANSLITERATION_EQUIV",
                sim,
                "same letters once accents are folded (e.g. ü typed as u)",
                req,
                act,
            )
        return _result(
            Outcome.MATCH, "SPACING_EQUIV", sim, "differs only in spacing or hyphens", req, act
        )
    if sorted(req.core_tokens) == sorted(act.core_tokens):
        return _result(
            Outcome.CLOSE_MATCH,
            "TOKEN_REORDER",
            sim,
            "same words in a different order",
            req,
            act,
        )
    if _is_truncation(req.core, act.core):
        return _result(
            Outcome.CLOSE_MATCH, "TRUNCATION", sim, "one name is a cut-off prefix", req, act
        )
    if sim >= CLOSE_RATIO:
        return _result(
            Outcome.CLOSE_MATCH,
            "EDIT_DISTANCE_CLOSE",
            sim,
            f"similarity {sim:.2f} >= {CLOSE_RATIO}; likely a typo",
            req,
            act,
        )
    return _overlap_or_none(req, act, sim)


def match_names(requested: str, actual: str, payee_type: PayeeType) -> MatchResult:
    """Compare the name the payer typed with the account holder's registered name."""
    req = normalize(requested, payee_type)
    act = normalize(actual, payee_type)
    if not req.core_tokens or not act.core_tokens:
        return _result(
            Outcome.NOT_POSSIBLE,
            "EMPTY_NAME",
            0.0,
            "a name is empty after normalisation",
            req,
            act,
        )
    base = _match_person(req, act) if payee_type is PayeeType.PERSON else _match_entity(req, act)
    if base.outcome is Outcome.MATCH and (req.address_removed or act.address_removed):
        return base.model_copy(
            update={
                "outcome": Outcome.CLOSE_MATCH,
                "rule_id": "ADDRESS_IN_NAME",
                "explanation": "names agree once an address was removed from the name field "
                f"({req.address_removed or act.address_removed!r})",
            }
        )
    return base


def lei_check_digits_valid(lei: str) -> bool:
    """ISO 17442 LEI check: 20 alphanumerics whose mod-97 value (A=10..Z=35) equals 1."""
    code = normalize_identifier(lei)
    if len(code) != 20 or not code.isalnum() or not code[-2:].isdigit():
        return False
    digits = "".join(str(int(ch, 36)) for ch in code)
    return int(digits) % 97 == 1


def lei_with_check_digits(base18: str) -> str:
    """Append the two LEI check digits to an 18-character base (used for synthetic data)."""
    base = normalize_identifier(base18)
    if len(base) != 18 or not base.isalnum():
        raise ValueError("LEI base must be 18 alphanumeric characters")
    digits = "".join(str(int(ch, 36)) for ch in base + "00")
    return f"{base}{98 - int(digits) % 97:02d}"


def match_identifier(requested: str, registered: dict[str, str]) -> MatchResult:
    """Check an organisation ID code against the codes on file.

    Only MATCH or NO_MATCH: an identifier either is or is not the one registered.
    Codes that look like an LEI (20 characters) must also pass the LEI check digits.
    """
    code = normalize_identifier(requested)
    on_file = {normalize_identifier(v): k for k, v in registered.items()}
    if len(code) == 20 and not lei_check_digits_valid(code):
        return MatchResult(
            outcome=Outcome.NO_MATCH,
            rule_id="ID_INVALID_FORMAT",
            similarity=0.0,
            explanation="20-character code fails the LEI check digits",
        )
    if not on_file:
        return MatchResult(
            outcome=Outcome.NO_MATCH,
            rule_id="ID_NOT_ON_FILE",
            similarity=0.0,
            explanation="the account holder has no identifier on file",
        )
    if code in on_file:
        return MatchResult(
            outcome=Outcome.MATCH,
            rule_id="ID_EXACT",
            similarity=1.0,
            explanation=f"identifier equals the registered {on_file[code]}",
        )
    return MatchResult(
        outcome=Outcome.NO_MATCH,
        rule_id="ID_MISMATCH",
        similarity=0.0,
        explanation="identifier differs from every code on file",
    )
