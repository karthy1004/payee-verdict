# Architecture

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | Pydantic types and enums: `Outcome`, `Payout`, `NormalisedName`, `MatchResult`, `CheckResult`, `DecisionContext`, `Decision`. | — |
| `normalize.py` | Traced, deterministic name normalisation; legal-form and honorific tables; `normalize_identifier`. | models |
| `matcher.py` | Reference matcher: ordered, named rules for people and entities; ID-code check with LEI check digits; Levenshtein. | models, normalize |
| `registry.py` | Load and validate the synthetic account registry. | models |
| `adapters/` | Simulated EU VoP, UK CoP, India name lookup and PIX key lookup, each mapped to `CheckResult`. | matcher, registry |
| `policy.py` | Versioned YAML policy, validation on load, pure `decide(context, policy)`. | models |
| `messages.py` | Payer-facing text per outcome. | models |
| `evidence.py` | Hash-chained evidence log, `verify()`, `replay()`, `render_diff()`. | models, policy |
| `engine.py` | `verify_payout`: adapter → context → policy → message → evidence. | adapters, policy, messages, evidence |
| `bulk.py` | CSV/JSONL input, aggregated counts, seeded synthetic payout generator. | engine |
| `corpus.py` | Load the labelled corpus, run the matcher, per-category report, gates. | matcher |
| `cli.py` | `verify`, `bulk`, `generate`, `corpus`, `replay`, `diff-policy`, `demo`. | all |

Nothing in `src/` opens a network connection. The only I/O is reading YAML, CSV or JSONL and writing reports or evidence when asked.

## One payout, end to end

```mermaid
sequenceDiagram
    participant P as Payout
    participant E as engine.verify_payout
    participant A as Adapter (EU/UK/IN/PIX)
    participant M as matcher
    participant Pol as policy.decide
    participant L as EvidenceLog
    P->>E: system, account, name, type, amount, bulk, id code
    E->>A: check(payout)
    alt responder decides (EU VoP, UK CoP)
        A->>M: match_names / match_identifier (simulated payee bank)
        M-->>A: outcome + rule ID + traces
    else lookup returns a name (India, PIX)
        A->>M: match_names locally (matched_locally = true)
        M-->>A: outcome + rule ID + traces
    else timeout / not found / flagged / unavailable
        A-->>A: NOT_POSSIBLE + reason code
    end
    A-->>E: CheckResult (holder name only if CLOSE_MATCH)
    E->>Pol: DecisionContext(system, outcome, reason, amount, currency, type, bulk)
    Pol->>Pol: fallbacks → rules (first match) → default
    Pol-->>E: Decision(verdict, rule, policy version, fallback?)
    E->>L: append(input hash, traces, raw response, outcome, rule, policy, verdict)
    L-->>E: record with prev_hash + hash
```

## Matcher rule order

```mermaid
flowchart TD
    S[normalise both names] --> Z{either empty?}
    Z -- yes --> NP[NOT_POSSIBLE · EMPTY_NAME]
    Z -- no --> X{keys equal?}
    X -- yes --> M1[MATCH · EXACT_NORMALISED]
    X -- no --> T{payee type}
    T -- ENTITY --> C{core equal, accent-folded<br/>or spacing-equal?}
    C -- "yes, legal forms differ" --> CM0[CLOSE_MATCH · LEGAL_FORM_CONFLICT]
    C -- "yes, same or missing form" --> M2[MATCH · LEGAL_FORM_EQUIV /<br/>TRANSLITERATION_EQUIV / SPACING_EQUIV]
    C -- no --> R
    T -- PERSON --> F{folded or spacing equal?}
    F -- yes --> M3[MATCH · TRANSLITERATION_EQUIV / SPACING_EQUIV]
    F -- no --> R{same tokens,<br/>other order?}
    R -- yes --> CM1[CLOSE_MATCH · TOKEN_REORDER]
    R -- no --> I{"person: initials or<br/>missing middle name?"}
    I -- yes --> CM2[CLOSE_MATCH · INITIALS / MISSING_MIDDLE_NAME]
    I -- no --> TR{prefix truncation?}
    TR -- yes --> CM3[CLOSE_MATCH · TRUNCATION]
    TR -- no --> ED{similarity ≥ 0.85?}
    ED -- yes --> CM4[CLOSE_MATCH · EDIT_DISTANCE_CLOSE]
    ED -- no --> NM[NO_MATCH · PARTIAL_TOKEN_OVERLAP / NO_TOKEN_OVERLAP]
```

A MATCH reached only after an address was stripped from the name is downgraded to `CLOSE_MATCH · ADDRESS_IN_NAME`. The UK CoP simulator downgrades a MATCH on the wrong account type (personal vs business) to `CLOSE_MATCH · ACCOUNT_TYPE_MISMATCH`.

## Determinism

- `normalize`, `match_names`, `match_identifier` and `decide` are pure functions.
- Similarity is rounded to 4 decimal places, so evidence hashes are stable.
- The EU VoP adapter takes an injectable latency model. Tests simulate a 60-second responder without sleeping.
- The engine takes an injectable clock. The demo and tests use a fixed clock, so their evidence hashes are reproducible.
- Synthetic payouts come from a seeded `random.Random`.

## What a record looks like

One line of the evidence JSONL, abbreviated:

```json
{"seq":0,"at":"2026-10-10T09:00:00Z","payout_id":"DEMO-01",
 "input_hash":"…64 hex…","system":"EU_VOP",
 "context":{"system":"EU_VOP","outcome":"MATCH","reason_code":null,"amount":"2500",
            "currency":"EUR","payee_type":"ENTITY","bulk":false},
 "normalisation":{"requested":[…],"actual":[{"step":"transliterate","before":"Müller GmbH","after":"Mueller GmbH"},…]},
 "raw_response":{"status":"ANSWERED","checked":"name","result":"MATCH","responder_rule":"EXACT_NORMALISED",…},
 "outcome":"MATCH","reason_code":null,"rule_id":"EXACT_NORMALISED","matched_locally":false,
 "account_holder_name_shown":null,"policy_id":"payout-default","policy_version":"1.0.0",
 "policy_rule_id":"R02-match-proceed","verdict":"PROCEED","fallback_applied":null,
 "payer_message":"The name matches the account holder.","prev_hash":"000…0","hash":"…"}
```
