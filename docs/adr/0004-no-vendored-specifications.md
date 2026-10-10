# ADR-0004: Link to scheme specifications, never vendor them

- Status: accepted
- Date: 2026-10-10

## Context

The EPC VoP rulebook and inter-PSP API specification, Pay.UK's Confirmation of Payee standards, NPCI/RBI documents, BCB's DICT documentation, and Swift/ISO 20022 message definitions are the real references for this domain. Their redistribution terms were not checked for this project. The EPC matching guidance (EPC288-23) could not be read first-hand (HTTP 403 during research).

## Decision

- No specification file, schema, code list or excerpt from those bodies is copied into this repository.
- Response shapes and reason codes in the simulated adapters are this project's own, and each adapter's docstring says so.
- The README links to the public pages instead.
- The matching rules are called "reference heuristics". The project does not claim compliance or conformance with any scheme.

## Consequences

- No licence risk from redistributing scheme documents.
- The simulators are illustrative and will not interoperate with a real scheme without a mapping layer written against the real specification.
- Anyone adopting the heuristics must check them against the scheme guidance they are bound by.
