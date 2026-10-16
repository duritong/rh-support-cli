# Potential Future Work

This document describes possible improvements, why they would be useful, and what is needed to deliver them. It is a planning and handoff guide, not a release commitment or authorization to change user workflows.

Only outstanding work is included. Existing case navigation, TUI research, draft management, handoff exports, diagnostics, and verification infrastructure are described in the [User Guide](user_guide.md) and [Developer Guide](developer_guide.md), not repeated here as future features.

## Design Boundaries

- Follow the [project vision](vision.md): reusable library operations, thin CLI/TUI presenters, responsive background work, local-first behavior, content fidelity, and offline-capable tests.
- Preserve Python 3.9 compatibility. Do not use a newer local interpreter as the only compatibility check.
- Agree on scope and acceptance criteria before implementation. Changes to flags, shortcuts, defaults, output, confirmation, or recovery behavior need an explicit user-experience proposal.
- Keep work incremental. Do not introduce a general framework or undertake a broad refactor without a concrete use case.
- Default diagnostics and tests to local, isolated operation. Production probes need a bounded plan and explicit operator authorization; remote writes need separate approval.
- Keep credentials and presigned URLs out of fixtures, logs, reports, and ordinary recovery metadata. Minimize retained customer content, protect it as private data, and sanitize shared fixtures or diagnostic evidence.
- Following a case remains local tracking. Remote notification-recipient management is a separate proposal requiring a supported vendor contract, not an implied extension of this plan.

## Execution Readiness Matrix

| # | Candidate | Area | Execution Readiness | Primary Blocker / Prerequisite |
| :--- | :--- | :--- | :--- | :--- |
| **4** | [Trusted Validation Errors](#4-trusted-validation-errors) | Library | **Ready to Implement** | Independent of production access; needs exception hierarchy design. |
| **7** | [Continuous Dependency-Floor Validation](#7-continuous-dependency-floor-validation) | Verification | **Ready to Implement** | CI matrix configuration in `.github/workflows/test.yml`. |
| **5** | [Library-Level Template Planning](#5-library-level-template-planning-and-application) | Library | **Needs Concrete Scope** | Requires a concrete multi-presenter reuse driver before abstraction. |
| **6** | [Client-Scoped Debug Logging](#6-client-scoped-debug-logging) | Architecture | **Under Investigation** | Isolation tradeoff analysis vs existing streaming/redaction hooks. |
| **9** | [Severity-Aware Follow-Up Guidance](#9-severity-aware-follow-up-guidance) | Workflow | **Needs UX Proposal** | Up-to-date Red Hat severity policy verification + UX review. |
| **8** | [Recoverable Transfer Jobs](#8-recoverable-transfer-jobs) | Workflow | **Deferred** | Blocked on Item 3 attachment service contracts. |
| **1** | [Authoritative GraphQL Schema](#1-authoritative-graphql-schema-and-version-aware-lookups) | Contract | **Blocked** | Blocked on vendor schema documentation (`INTROSPECTION_DISABLED`). |
| **2** | [Controlled Remote Contract Checks](#2-controlled-remote-contract-checks) | Verification | **Blocked** | Requires operator authorization + safe test targets. |
| **3** | [Attachment Contract & Integrity](#3-attachment-contract-and-integrity-guarantees) | Contract | **Blocked** | Requires vendor API contract documentation for ETags/multipart. |

## Contract Verification

### 1. Authoritative GraphQL Schema and Version-Aware Lookups

**Idea:** Validate GraphQL operations against an approved vendor schema and resolve product/version combinations using supported filters rather than assumptions about the remote API.

**Why:** A successful offline request simulation does not establish that a query, input type, relationship, or permission is valid in production. Version-aware case creation depends on these definitions.

**Prerequisites:** Production introspection has returned `INTROSPECTION_DISABLED`. Obtain a permitted schema export or authoritative contract documentation from Red Hat. Required information includes product/version filters, creation inputs, relationship constraints, and the applicable endpoint/version. Do not work around disabled introspection by guessing operations.

**Execution:**
- Record the contract source, permission to retain it, and applicable API version or environment. Store permitted metadata, never credentials or customer records.
- Build an offline schema fixture and validate application query documents and variables against it.
- Implement product/version resolution after the filter and uniqueness rules are known. Preserve fail-closed behavior for ambiguous, missing, or conflicting results.
- Review the experimental `inspection/support-schema` branch before reusing its diagnostic tooling; it is not a shipped command on `main`.

**Acceptance:** Valid operations pass schema validation; invalid fields, variables, and inputs fail offline. Lookup tests cover exact matches, ambiguity, missing versions, and account/contact conflicts. Document the scope of the evidence rather than claiming compatibility with every deployment.

**Status:** Blocked on authoritative, permitted API metadata.

### 2. Controlled Remote Contract Checks

**Idea:** Supplement offline tests with small, reproducible checks of actual endpoint behavior and permissions.

**Why:** Schema validation cannot prove that a deployed gateway permits an operation or that pagination, errors, and permissions behave as expected for an account.

**Prerequisites:** An operator-approved environment and identity, an agreed read scope, and a reviewed plan listing endpoints, fields, maximum records/pages, timeouts, expected output, and local side effects. Any mutation test needs separate authorization and a designated safe target.

**Execution:**
- Begin with read-only probes that print their plan before execution. Reuse normal authentication and sanitization without writing credentials or raw responses to diagnostic output.
- Capture the minimum contract evidence needed for a specific question. Redact customer content and identifiers before creating fixtures.
- Distinguish reachability, authentication, permission, schema compatibility, and successful data retrieval. A reachable endpoint is not proof of API health.
- Before requesting a write check, document its exact mutation, duplicate-prevention strategy, possible ambiguous outcome, and recovery procedure.

**Acceptance:** Probes are bounded, explicit, and reproducible; default operation cannot mutate remote state. Approved evidence becomes sanitized regression coverage where possible. Failures remain identifiable without exposing secrets.

**Status:** Requires operator authorization; some checks also depend on the schema work above.

### 3. Attachment Contract and Integrity Guarantees

**Idea:** Establish which sizing, manifest, ETag, checksum, and source-stability guarantees the actual attachment service supports.

**Why:** Byte counts establish coverage, not cryptographic integrity or protection against same-size concurrent source edits. An ETag must not be treated as a checksum without a documented contract.

**Prerequisites:** Authoritative documentation or approved contract evidence, plus a decision on the integrity guarantee users need. Define whether modifying a source during upload is unsupported, detectable, or recoverable.

**Execution:**
- Verify part-size/number rules, completion semantics, checksums, session expiry, and the meaning of ETags.
- Assess source identity checks, immutable snapshots, or incremental hashing. Avoid an unconditional extra read of multi-terabyte inputs without justification.
- Extend loopback tests with corruption, source modification, failed completion, and cancellation cases reflecting the verified contract.
- If larger-scale benchmarking is needed, define sizes, concurrency, transport conditions, memory budgets, and cleanup before execution. Existing loopback memory tests do not prove a real 5 TB transfer.

**Acceptance:** Each claimed integrity guarantee has a documented basis and a test that detects its violation. Memory claims identify measured conditions and distinguish bounded transfer reads from explicit read-all calls.

**Status:** Contract verification is externally dependent; requirements analysis and local experiments can proceed independently.

## Library and Verification Improvements

### 4. Trusted Validation Errors

**Idea:** Replace generic `ValueError` exceptions and untyped error checklists with a structured, library-wide validation exception hierarchy across case creation, status updates, and attachments.

**Why:** Presenters currently differentiate validation errors from unexpected runtime failures through fragile string heuristics or by catching broad `ValueError` exceptions. A structured hierarchy allows programmatic error handling, secret-safe diagnostics, and clean separation between local validation failures, remote-read errors, and partial mutation outcomes.

**Prerequisites:** Inventory all mutation preconditions (case creation lookups, comment/status transitions, attachment bounds) and define a consistent base hierarchy:
- `RHSupportError` (library root exception)
  - `ValidationError` (local input or routing constraint violation)
    - `CaseLookupConflictError` (contact/account routing mismatch)
    - `AmbiguousEntityError` (unresolvable product or version match)
    - `InvalidTransitionError` (illegal case status update)
    - `AttachmentValidationError` (missing file, invalid part slicing bounds)

**Execution:** Introduce the exception hierarchy at the library boundary (`rh_support_lib/errors.py`). Populate safe diagnostic attributes (field names, allowed choices, rejected inputs) without leaking internal payloads or credentials. Update CLI and TUI presenters to format these structured exceptions without altering successful execution flows.

**Acceptance:** Tests verify specific exception classes are raised before remote network dispatch occurs; presenters display clean, secret-safe error messages without stack traces; automation handles structured attributes cleanly.

**Status:** Ready for scoping; completely independent of production access.
### 5. Library-Level Template Planning and Application

**Idea:** Expose planning and execution as reusable library operations so CLI and TUI share one supported-field, comparison, and mutation contract.

**Why:** Presenter-specific orchestration can drift as fields, dry-run output, or callers are added. A shared plan would make behavior assessable without capturing CLI output.

**Prerequisites:** Identify a concrete duplication or consumer that justifies extraction. Define plan contents, validation, results, and stale-plan handling before adding abstractions.

**Execution:** Move only reusable rendering/validation/difference/application behavior into the library. Keep output and confirmation in presenters. Limit execution to verified fields; do not silently expand portal metadata or watcher support. Decide whether execution recomputes the plan or validates its source case version.

**Acceptance:** All callers use the same supported-field contract. Dry runs never mutate, unsupported fields fail before mutation, and stale plans cannot silently overwrite newer changes.

**Status:** Requires a concrete scope; not a general application rewrite.

### 6. Client-Scoped Debug Logging

**Idea:** Evaluate replacing the process-wide Requests hook with logging owned by a client or session.

**Why:** A global hook can affect unrelated Requests consumers and makes independent clients harder to reason about. A replacement must preserve streaming and redaction safety.

**Prerequisites:** Inventory HTTP paths, including SSO and presigned transfers, and decide which must be logged. Define ownership, repeated enable/disable behavior, concurrency, and log-file lifecycle.

**Execution:** Add the smallest client/session-level mechanism covering those paths. Preserve bounded logging, sanitized headers/URLs, opaque-body treatment, response ownership, and authentication/replay behavior. Never consume streamed bodies for diagnostics.

**Acceptance:** Clients can use independent settings without changing unrelated Requests calls. Tests cover concurrent/repeated setup, signed URLs, secrets, nonseekable bodies, streamed responses, and cleanup. Document any changes to existing debug output.

**Status:** Architectural investigation; implement only if the isolation benefit justifies the change.

### 7. Continuous Dependency-Floor Validation

**Idea:** Turn manually verified minimum-dependency environments into automated, continuous CI verification alongside current compatible dependencies in `.github/workflows/test.yml`.

**Why:** Checking dependency floors once does not prevent future changes from inadvertently requiring newer APIs. Standard CI dependency resolution installs the newest compatible packages, masking minimum-version regressions.

**Prerequisites:** Use declared `install_requires` floors in `setup.py` and the verified combinations in the Developer Guide. Define a pinned constraint file (`requirements-min.txt`) or a `--no-deps` installation strategy so `pip` cannot resolve newer transitive packages during floor verification.

**Execution:** Add a dedicated `test-min-deps` matrix job to `.github/workflows/test.yml` running against supported Python runtimes. Install floor constraints explicitly, verify the environment offline via `tests.support.SandboxSuite`, and execute the console script entry point and completion checks.

**Acceptance:** CI automatically flags code using APIs introduced in newer versions of dependencies than our declared floors. Minimum and latest-compatible CI runs remain distinct and green.

**Status:** Ready to implement in GitHub Actions workflow.
## Operator Workflows

### 8. Recoverable Transfer Jobs

**Idea:** Extend existing streaming, progress, cancellation, and failure handling into a persistent job lifecycle that can be inspected and safely continued across interruptions or restarts.

**Why:** Large-bundle uploads need an explicit account of completed, failed, and uncertain stages, plus safe expiry/recovery behavior that avoids duplicate attachments.

**Prerequisites:** Verify service support for session inspection, multipart continuation, abort, completion checks, and replacing expired capabilities. Define behavior when identity or source content changes. If remote resume is unsupported, offer an honest restart workflow rather than calling it resume.

**Execution:**
- Define job states and legal transitions, including pending, transferring, completion-unconfirmed, completed, cancelled, and failed.
- Persist necessary private metadata: identity/backend binding, source identity, safe remote identifiers, and confirmed stages. Do not store reusable presigned URLs as ordinary job data.
- Reconcile remote state before retrying ambiguous completion. Reacquire capabilities only through verified operations.
- Expose reusable operations to CLI/TUI, keeping I/O in workers and results tied to the correct case/job generation.

**Acceptance:** Tests cover restart, expiry, cancellation, changed sources, identity switches, and ambiguous completion without duplicate creation. Recovery remains memory-bounded, preserves capability secrecy, and reports unsupported operations explicitly.

**Status:** Depends on attachment contracts and explicit user-experience approval.

### 9. Severity-Aware Follow-Up Guidance

**Idea:** Offer accurate follow-up guidance after urgent-case creation when current Red Hat policy recommends an additional action such as a phone call.

**Why:** Online submission may not be the entire operational process for an urgent incident. Guidance can help an on-call operator without implying escalation or a response commitment.

**Prerequisites:** Verify current [Red Hat severity guidance](https://access.redhat.com/support/policy/severity), contact references, and regional/entitlement differences. Agree on timing and interaction with JSON/YAML receipts, quiet modes, and automation.

**Execution:** Use a small, testable policy decision based on verified severity definitions and confirmed creation. Keep explanatory guidance out of machine-readable stdout. Provide maintained policy/contact references instead of hardcoded promises about escalation, response time, or entitlement.

**Acceptance:** Tests cover urgent/nonurgent severities, failed/partially successful creation, and structured output. Messages match verified policy, never imply a call was placed, and can be maintained when guidance changes.

**Status:** Requires policy verification and explicit user-experience approval.

## Selecting and Delivering Work

1. Confirm that the proposed gap still exists and identify the smallest useful outcome.
2. Agree on prerequisites, acceptance criteria, compatibility, and any UX or remote-access approval.
3. Prefer independent offline work while contract questions are blocked. Trusted validation errors and dependency-floor CI are candidates; transfer resume should wait for its service contract.
4. Add focused evidence or regression tests before implementation where applicable. Test behavior rather than relying on success-shaped mocks.
5. Update the User Guide for supported workflows and the Developer Guide for implementation contracts. Use the canonical runner and required Ruff checks described there.
6. Remove delivered items or narrow them to remaining work. Keep implementation history in commits and maintained guides, not a growing remediation ledger.
