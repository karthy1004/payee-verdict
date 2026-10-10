"""Deterministic, traced name normalisation.

Every step is recorded as a `TraceStep(step, before, after)`, so a reviewer can see
exactly why two names compared equal or not. The steps, in order:

1. ``unicode_nfc``      compose characters (so a + combining umlaut equals the single letter)
2. ``transliterate``    German/Nordic conventions: ü→ue, ö→oe, ä→ae, ß→ss, æ→ae, ø→oe, å→aa
3. ``strip_diacritics`` remove any remaining accents (é→e, ç→c, ñ→n, ã→a)
4. ``uppercase``
5. ``strip_address``    drop a comma/semicolon-separated tail that contains a digit
6. ``ampersand``        & → AND
7. ``punctuation``      apostrophes removed, all other punctuation becomes a space
8. ``whitespace``       collapse runs of spaces
9. ``honorifics``       (people) drop leading MR, MRS, DR, SHRI, HERR and similar
10. ``legal_form``      (entities) detect and remove one trailing legal form, record its family
11. ``noise_words``     (entities) drop THE and AND

`folded_core` repeats the pipeline without step 2, so "Müller" folds to "MULLER". The
matcher uses it to treat "Muller" and "Müller" as the same spelling.

These are reference heuristics written for this project. They are not the EPC, Pay.UK,
NPCI or BCB matching rules, none of which is reproduced here.
"""

from __future__ import annotations

import re
import unicodedata

from payee_verdict.models import NormalisedName, PayeeType, TraceStep

TRANSLITERATION: dict[str, str] = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
    "ẞ": "SS",
    "æ": "ae",
    "Æ": "Ae",
    "œ": "oe",
    "Œ": "Oe",
    "ø": "oe",
    "Ø": "Oe",
    "å": "aa",
    "Å": "Aa",
}

# Characters NFKD does not decompose; folded to plain letters when transliteration is off.
_FOLD_EXTRA: dict[str, str] = {"ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE"}
_FOLD_EXTRA |= {"ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L"}

HONORIFICS: frozenset[str] = frozenset(
    {
        "MR",
        "MRS",
        "MS",
        "MISS",
        "MX",
        "DR",
        "PROF",
        "SIR",
        "DAME",
        "HERR",
        "FRAU",
        "SR",
        "SRA",
        "SRTA",
        "DONA",
        "MME",
        "MLLE",
        "SHRI",
        "SRI",
        "SMT",
        "KUM",
    }
)

# Legal-form family -> surface token sequences (after punctuation becomes spaces).
LEGAL_FORMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "GMBH_CO_KG": (("GMBH", "AND", "CO", "KG"), ("GMBH", "CO", "KG")),
    "GMBH": (("GMBH",), ("GESELLSCHAFT", "MIT", "BESCHRAENKTER", "HAFTUNG")),
    "AG": (("AG",), ("AKTIENGESELLSCHAFT",)),
    "KG": (("KG",),),
    "PVT_LTD": (
        ("PVT", "LTD"),
        ("PVT", "LIMITED"),
        ("PRIVATE", "LIMITED"),
        ("PRIVATE", "LTD"),
    ),
    "PLC": (("PLC",), ("PUBLIC", "LIMITED", "COMPANY")),
    "LTD": (("LTD",), ("LIMITED",)),
    "LLC": (("LLC",), ("L", "L", "C")),
    "LLP": (("LLP",), ("L", "L", "P")),
    "INC": (("INC",), ("INCORPORATED",)),
    "CORP": (("CORP",), ("CORPORATION",)),
    "LTDA": (("LTDA",), ("LIMITADA",)),
    "SARL": (("SARL",), ("S", "A", "R", "L")),
    "SAS": (("SAS",), ("S", "A", "S")),
    "SA": (
        ("SA",),
        ("S", "A"),
        ("SOCIEDADE", "ANONIMA"),
        ("SOCIEDAD", "ANONIMA"),
        ("SOCIETE", "ANONYME"),
    ),
    "SPA": (("SPA",), ("S", "P", "A")),
    "SRL": (("SRL",), ("S", "R", "L")),
    "BV": (("BV",), ("B", "V")),
    "NV": (("NV",), ("N", "V")),
    "CO": (("CO",), ("COMPANY",)),
}

# Longest sequences first, so "PVT LTD" wins over "LTD".
_LEGAL_FORM_INDEX: tuple[tuple[tuple[str, ...], str], ...] = tuple(
    sorted(
        ((seq, family) for family, seqs in LEGAL_FORMS.items() for seq in seqs),
        key=lambda item: -len(item[0]),
    )
)

ENTITY_NOISE_WORDS: frozenset[str] = frozenset({"THE", "AND"})

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _strip_diacritics(text: str, extra: dict[str, str] | None = None) -> str:
    if extra:
        text = "".join(extra.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize(
        "NFC", "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    )


def _strip_address(text: str) -> tuple[str, str | None]:
    parts = re.split(r"[,;]", text)
    for i in range(1, len(parts)):
        if any(ch.isdigit() for ch in parts[i]):
            kept = ",".join(parts[:i]).strip()
            removed = ",".join(parts[i:]).strip()
            return kept, removed
    return text, None


def _punctuation(text: str) -> str:
    for apostrophe in ("'", "\N{RIGHT SINGLE QUOTATION MARK}", "`"):
        text = text.replace(apostrophe, "")
    return _PUNCT.sub(" ", text)


def _find_legal_form(tokens: list[str]) -> tuple[str, int] | None:
    """Return (family, length) of a trailing legal form, keeping at least one core token."""
    for seq, family in _LEGAL_FORM_INDEX:
        n = len(seq)
        if len(tokens) > n and tuple(tokens[-n:]) == seq:
            return family, n
    return None


class _Tracer:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    def record(self, step: str, before: str, after: str) -> str:
        self.steps.append(TraceStep(step=step, before=before, after=after))
        return after


def _pipeline(
    name: str, payee_type: PayeeType | None, transliterate: bool
) -> tuple[list[str], str | None, str | None, str | None, list[TraceStep]]:
    t = _Tracer()
    s = t.record("unicode_nfc", name, unicodedata.normalize("NFC", name))
    if transliterate:
        s = t.record("transliterate", s, "".join(TRANSLITERATION.get(ch, ch) for ch in s))
        s = t.record("strip_diacritics", s, _strip_diacritics(s))
    else:
        s = t.record("strip_diacritics", s, _strip_diacritics(s, _FOLD_EXTRA))
    s = t.record("uppercase", s, s.upper())
    kept, removed = _strip_address(s)
    s = t.record("strip_address", s, kept)
    s = t.record("ampersand", s, s.replace("&", " AND "))
    s = t.record("punctuation", s, _punctuation(s))
    s = t.record("whitespace", s, _SPACES.sub(" ", s).strip())
    tokens = s.split(" ") if s else []

    if payee_type is not PayeeType.ENTITY:
        before = " ".join(tokens)
        while len(tokens) > 1 and tokens[0] in HONORIFICS:
            tokens = tokens[1:]
        t.record("honorifics", before, " ".join(tokens))

    legal_family: str | None = None
    legal_surface: str | None = None
    if payee_type is not PayeeType.PERSON:
        before = " ".join(tokens)
        found = _find_legal_form(tokens)
        if found:
            legal_family, n = found
            legal_surface = " ".join(tokens[-n:])
            tokens = tokens[:-n]
        t.record("legal_form", before, " ".join(tokens))
        if payee_type is PayeeType.ENTITY:
            before = " ".join(tokens)
            tokens = [tok for tok in tokens if tok not in ENTITY_NOISE_WORDS]
            t.record("noise_words", before, " ".join(tokens))

    return tokens, legal_family, legal_surface, removed, t.steps


def normalize(name: str, payee_type: PayeeType | None = None) -> NormalisedName:
    """Normalise a payee name and return the full trace.

    `payee_type=None` applies both the person steps (honorifics) and the entity step
    (legal form), which is what you want when the type is unknown.
    """
    tokens, family, surface, removed, trace = _pipeline(name, payee_type, transliterate=True)
    folded_tokens, *_ = _pipeline(name, payee_type, transliterate=False)
    key_tokens = [*tokens, *(surface.split(" ") if surface else [])]
    return NormalisedName(
        original=name,
        key=" ".join(key_tokens),
        core_tokens=tuple(tokens),
        folded_core=" ".join(folded_tokens),
        legal_form=family,
        legal_form_surface=surface,
        address_removed=removed,
        trace=tuple(trace),
    )


def normalize_identifier(code: str) -> str:
    """Uppercase and drop spaces, dots, hyphens and slashes from an ID code."""
    return re.sub(r"[\s.\-/]", "", code).upper()
