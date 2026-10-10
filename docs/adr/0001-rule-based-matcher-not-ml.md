# ADR-0001: A rule-based reference matcher, not a learned model

- Status: accepted
- Date: 2026-10-10

## Context

A payee-name check produces one of four outcomes. A wrong MATCH lets money go to the wrong account with no warning; a wrong NO_MATCH blocks a legitimate payment and trains payers to ignore warnings. Operations, compliance and the payer all need to know *why* a pair got its outcome, and the answer must be the same tomorrow.

There is no public labelled dataset of real payee-name pairs, and building one from real names would mean processing personal data.

## Decision

The matcher is an ordered list of named rules (`EXACT_NORMALISED`, `LEGAL_FORM_EQUIV`, `TOKEN_REORDER`, `INITIALS`, `EDIT_DISTANCE_CLOSE`, ...). The first rule that applies decides, and its ID is returned with every result. People and legal entities have separate rule lists. Normalisation is a fixed pipeline whose every step is traced.

Thresholds (similarity 0.85, truncation minimum 10 characters and 50% of the longer name) are module constants, visible in code review.

## Consequences

- Every outcome is explainable by a rule ID plus a normalisation trace, and is deterministic.
- The heuristics are the author's, not any scheme's. They are called "reference heuristics" everywhere, and the project claims no EPC, Pay.UK, NPCI, RBI or BCB conformance.
- Known weaknesses are measured, not hidden: nicknames, acronyms and reordered initials fail (`known_gap` in the corpus), and the truncation rule over-matches a holding company to its operating company (`DE-11`).
- A learned model could later sit *behind* the rules as a candidate generator, but only if its output is reduced to a rule ID and a reason, and only once there is a lawful labelled dataset to evaluate it against.
