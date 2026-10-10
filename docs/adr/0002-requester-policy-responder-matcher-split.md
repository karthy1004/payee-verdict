# ADR-0002: Split the responder's matcher from the requester's policy

- Status: accepted
- Date: 2026-10-10

## Context

In EU Verification of Payee and UK Confirmation of Payee, the **payee's** bank (the responder) decides the match. The paying side (the requester) only receives an outcome. A sender-side engine that re-decided the match would duplicate, and might contradict, the bank's answer.

Other systems behave differently. The India RTGS/NEFT beneficiary-name lookup and the Brazil PIX key lookup return a *name*, not a decision, so somebody on the paying side has to compare names.

A payout platform that pays through all four needs one answer per payout: proceed, warn and confirm, or hold.

## Decision

Two separate layers:

1. **Responder side: `matcher`.** A reference implementation of what a payee's bank might run. The simulated EU and UK responders use it to produce their answer. For India and PIX the engine uses the same matcher locally on the returned name (`matched_locally: true` in the evidence).
2. **Requester side: `policy`.** A versioned YAML policy maps (system, outcome, reason code, amount band, payee type, bulk flag) to PROCEED / WARN_CONFIRM / HOLD. It never re-matches names. It only consumes the outcome.

Adapters translate each system's own response shape into one `CheckResult` model.

## Consequences

- For VoP and CoP the policy respects the responder's outcome. It cannot upgrade a NO_MATCH to a MATCH.
- The same policy file governs all four systems, so "what happens to a close match over 10,000 in a bulk file" has one answer.
- `reason_code` is a policy input in addition to the five dimensions in the spec, because a flagged PIX key and a timeout are both NOT_POSSIBLE but need different handling.
- Fallbacks, such as what to do when the India lookup is unavailable, are a separate policy section, so the evidence can record that a fallback rather than a normal rule decided.
