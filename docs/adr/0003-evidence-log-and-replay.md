# ADR-0003: Hash-chained evidence log with replay and policy diff

- Status: accepted
- Date: 2026-10-10

## Context

When a payment goes to the wrong account, someone will ask what the platform knew and why it paid. When the policy changes, someone will ask which past payouts would have been treated differently. Both questions need a record that can be trusted and re-used without calling the payment systems again.

## Decision

Every verification appends one record to an append-only log. A record holds:

- `input_hash`: SHA-256 of the canonical payout JSON
- `normalisation`: the changed normalisation steps for the entered and registered names
- `raw_response`: the system's response as received
- `outcome`, `reason_code`, `rule_id`, `matched_locally`
- `context`: the exact `DecisionContext` the policy saw
- `policy_id`, `policy_version`, `policy_rule_id`, `verdict`, `fallback_applied`
- `payer_message`, plus `seq`, `at`, `prev_hash`, `hash`

`hash` is SHA-256 over the canonical JSON of every other field, including `prev_hash`. `verify()` recomputes the chain. The design is adapted from evidence-review-engine's audit log.

`replay(log, policy)` re-runs the pure `decide(context, policy)` for every record. Under the policy that wrote the log, every verdict must be identical; the CLI exits non-zero if not. Under a different policy, the same function produces the diff report.

## Consequences

- Editing, deleting or reordering a record is detected (tested).
- Replay does not re-call adapters or re-run the matcher. It answers "what would this policy have decided given what we were told", which is the question a policy change raises. A matcher change needs a corpus run instead.
- Only *changed* normalisation steps are stored, which keeps a 10,000-record log at about 14 MB. The full trace is reproducible from the original name with `normalize()`.
- The log contains payee names, which are personal data. A real deployment needs retention limits and access control. This repository only ever writes synthetic names.
- It is tamper-evident, not tamper-proof: anyone who can rewrite the whole file can rebuild the chain. Anchor the head hash externally.
