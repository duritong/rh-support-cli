# Red Hat Support Case CLI - Developer Guide

Architecture, design decisions, API protocols, and internal subsystems for developers and contributors maintaining the CLI and library components.

---

## 1. Project Architecture & File Tree

The project is structured as a decoupled Python application where presentation layers (CLI and TUI) serve as thin wrappers over domain services and models:

```
rh-support-cli/
├── rh-support-cli.py           # Executable console script entrypoint
├── setup.py                    # Packaging metadata, entry points, install_requires
├── rh_support_lib/             # Core reusable library package
│   ├── api.py                  # RedHatAPIClient (OIDC SSO, GraphQL client, HTTP retry)
│   ├── main.py                 # CLI argument parsing, subcommands, and dispatching
│   ├── config.py               # YAML configuration loader (bookmarks, templates, cache)
│   ├── constants.py            # Shared API URLs, picklist status mappings, ANSI colors
│   ├── models.py               # Domain models (Case, CaseComment, Attachment, KCSSearchResult)
│   ├── completers.py           # Dynamic shell auto-completers (products, statuses, bookmarks)
│   ├── templates.py            # YAML Jinja2 template processing engine
│   ├── statuses.py             # Status normalization, mapping, and reply determination
│   ├── utils.py                # Formatting, rich-text conversion, ISO date helpers
│   ├── commands/               # CLI presentation commands
│   │   ├── actions.py          # comment and close subcommands
│   │   ├── apply_template.py   # apply and list-templates subcommands
│   │   ├── attachments.py      # attachment subcommands (list, upload, download, delete, status)
│   │   ├── create_case.py      # create subcommand
│   │   ├── doctor.py           # doctor diagnostic health check command
│   │   ├── drafts.py           # drafts discovery, preview, and cleanup commands
│   │   ├── list_cases.py       # list subcommand
│   │   ├── search.py           # search subcommand
│   │   ├── show_case.py        # show subcommand (Rich Panels and Markdown paging)
│   │   ├── systemd.py          # systemd and install-systemd unit management
│   │   ├── tracking.py         # sync and updates subcommands
│   │   └── watchers.py         # follow, follow-account, unfollow, unfollow-account, following
│   ├── services/               # Reusable business logic services
│   │   ├── attachments.py      # AttachmentService (v3 presigned upload, multipart, download)
│   │   ├── cache.py            # CacheService (Atomic XDG disk cache, SWR, TTL management)
│   │   ├── cases.py            # CaseService (GraphQL queries, mutations, case lookups)
│   │   ├── search.py           # SearchService (Unified Case & Hydra KCS search coordinator)
│   │   └── state.py            # StateService (Thread-safe state.yaml, atomic persistence)
│   ├── synthetic_corpus/       # Local stateful mock portal and synthetic generator
│   │   ├── generator.py        # Synthetic test corpus generator
│   │   └── server.py           # Stateful loopback mock HTTP and GraphQL server
│   └── tui/                    # Interactive Terminal User Interface (TUI)
│       └── app.py              # Textual dashboard, view-swapping containers, modals
└── tests/                      # Offline-capable automated test suite
```

---

## 2. Core Architecture & Decoupling

* **Library-First Domain Services**: All business logic lives in `rh_support_lib/services/`. Neither CLI commands (`commands/`) nor the TUI (`tui/`) execute raw API requests or directly manipulate state files. They consume normalized domain models from `models.py`.
* **Presentation-Only Commands**: CLI command modules handle argument parsing, terminal output formatting (via `rich`), and delegate operations to services.
* **Template Engine Contract**: `TemplateEngine` (`templates.py`) processes Jinja2-compatible YAML templates with `StrictUndefined` semantics. `apply` restricts mutations to supported mutable fields (`status`, `severity`, `summary`, `description`). Underscore-prefixed keys (e.g. `_template_description`) are treated as metadata and ignored by API mutations.
* **Structured Serialization**: All query commands provide pure JSON/YAML serialization via `format_structured_output` (`--json`, `--format yaml`), suppressing banners and ANSI formatting for clean pipe processing with tools like `jq`.
* **Local-First Operations**: Operations inspecting only local resources (e.g. `list-templates`, `completion`, or `search --local`) bypass OIDC token verification for instant execution without network latency.
* **Editor Approval Boundary**: Interactive editing uses `open_editor()` (`utils.py`), which launches `$EDITOR` without an intermediate shell. If the editor exits abnormally or content is empty, submissions halt safely and recovery drafts are retained under `$XDG_CONFIG_HOME/rh-support-cli/drafts/`.

---

## 3. API Protocols & Authentication

### Endpoint Overview

| Service | Protocol | Default URL | Purpose |
| :--- | :--- | :--- | :--- |
| **CRM GraphQL API** | GraphQL over HTTP POST | `https://graphql.redhat.com` | Case listing, details, comments, creation, lookups |
| **Attachment Service** | REST v3 / S3 Presigned URLs | `https://api.access.redhat.com/support` | Presigned single/multipart upload, download, delete |
| **Hydra Search API** | REST over HTTP GET | `https://access.redhat.com/hydra/rest` | Knowledgebase (KCS) solutions and technical articles |
| **Red Hat SSO** | OIDC Token Endpoint | `https://sso.redhat.com/...` | Offline token exchange for short-lived access tokens |

### OIDC Authentication & Token Lifecycle


`RedHatAPIClient` (`api.py`) manages authentication via Red Hat SSO:
1. **Offline Token Exchange**: Exchanges an offline token for a short-lived access token (`POST /auth/token`).
2. **Token Caching & Fingerprinting**: Access tokens are cached under `$XDG_CACHE_HOME/rh-support-cli/tokens/` bound to an opaque SHA-256 fingerprint of the resolved credential, endpoint, and client ID.
3. **Transparent 401 Retries & Scope Detection**: Intercepts `401 Unauthorized` responses, invalidates the cached access token, performs an in-process refresh, and retries the request once. If the retried response explicitly reports `Required scope api.graphql not found in JWT token`, `RedHatAPIClient.request()` raises an `AuthenticationError` explaining that a new offline token must be generated at `https://access.redhat.com/management/api`. Other 401 responses propagate normally.
4. **Stream Rewinding**: Before sending requests with seekable payloads, stream offsets are captured so 401 retries can rewind and replay the body without buffering entire streams into memory. Non-seekable bodies raise `RequestReplayError` rather than retrying empty content.

### GraphQL CRM Protocol

* **Relay Cursor Pagination**: Queries use standardized Relay connection specifications (`edges { node, cursor }`, `pageInfo { hasNextPage, endCursor }`). `list_cases(all_pages=True)` exhausts all pages automatically.
* **Child Relationship Handling**: Salesforce GraphQL UIAPI does not support cursor pagination with `after` on child relationships (e.g. `CaseComments__r`). Subsequent comment batches fall back to root queries with `where: { Case__c: { eq: <id> } }`.
* **Lightweight Status Probing**: `check_case_last_modified(case_number)` executes a ~250-byte GraphQL probe query returning `(internal_id, last_modified_date, status, latest_comment_id, latest_comment_date)` without fetching full comment histories.

### Comment Posting & Live Status Resolution Contract

`CaseService.add_comment` adds a comment and optionally updates case status:
* If `status` is `None`, only the comment is posted (no status mutation is sent).
* If `status` is provided, `update_case` is called after the comment is created.
* If comment creation succeeds but status updating or cache invalidation fails, a `PartialCommentError` is raised. The comment is never reposted, and the caller is instructed to change status only.
* **Live Status Resolution (`get_case_status`)**: Resolves current status via a live lightweight probe (`check_case_last_modified`) followed by live uncached details (`get_case_details(..., use_cache=False)`). If live status cannot be read, it raises `ReadError` (or `AuthenticationError`); missing or unreachable status is never treated as a keep-status decision. Raw cached statuses must never authorize an implicit status mutation.
* **Default Comment Transitions**: In CLI and TUI, commenting defaults to transitioning status only when the case is in `Waiting on Customer` (or `Waiting on Customer Action Required`), moving it to `In Progress` (`STATUS_IN_PROGRESS`). For cases in other states, status remains untouched (`status=None`).
* **Post-Editor Live Recheck**: For interactive CLI comments, `actions.py` re-queries `case_service.get_case_status()` immediately after `$EDITOR` closes to ensure transitions reflect live remote state.

### Hydra KCS Search API

* Queries `https://access.redhat.com/hydra/rest/search/kcs?q=<term>` using Apache Solr parameters.
* Scopes queries by language (`fq=language:<lang>`) and product name (`fq=product:"<name>"`).
* Normalizes response documents into standard `KCSSearchResult` models.

---

## 4. Large Attachment Streaming & Multipart Engine

`AttachmentService` (`services/attachments.py`) handles files from kilobytes up to 5 TB:

```
                            File Upload Request
                                     │
                                     ▼
                    File Size > MULTIPART_THRESHOLD (5 GiB)?
                                     │
                    ┌────────────────┴────────────────┐
                    │ YES                             │ NO
                    ▼                                 ▼
         Initiate Multipart Upload           Initiate Single PUT
           (totalChunks calculated)           (presignedUrl returned)
                    │                                 │
                    ▼                                 ▼
         Stream 5 GiB Parts via               Stream Entire File
           FilePartStream (8 MiB blocks)        (FilePartStream)
                    │                                 │
                    ▼                                 ▼
         Collect Part ETags                   Complete Upload
                    │
                    ▼
         Complete Multipart Upload
           (/upload/complete)
```

### Low-Memory Streaming (`FilePartStream`)

To prevent `MemoryError` exceptions on large files:
* Positive-sized `read(size)` calls are bounded by `block_size` (default: 8 MiB), including Requests' transport reads. Explicit `read()` or `read(-1)` retains file-like read-all semantics and is not memory-bounded.
* Implements `__len__` returning the part's slice length, allowing `requests.put` to set `Content-Length` without buffering the entire part.
* Memory usage remains bounded to the block size regardless of total file size.

### Safe Download Publication

`download_attachment` streams the remote file into a private temporary file in the destination directory, verifies the declared unencoded length against `Content-Length`, flushes to disk, and publishes via `os.link`.
* **Hard-Link Requirement**: The destination filesystem must support hard links.
* **Destination Collision Refusal**: Atomically refuses destination collisions (existing files, symlinks, or files created concurrently during transfer) without overwriting.
* **Cleanup on Error**: Temporary files are removed and response streams closed on success, failure, callback exceptions, and interruption.

---

## 5. Rich Text & Comment Formatting Pipeline

Red Hat Customer Portal stores comment bodies and case descriptions as **HTML** (`Body__c`). The web UI renders this field using `innerHTML` without `white-space: pre-wrap`, meaning raw newlines (`\n`) collapse into whitespace unless formatted:

```
CLI / TUI Input (Markdown) ──► format_body_for_portal() ──► CRM HTML (Body__c)
                                                                    │
                                                                    ▼
Terminal Display (Markdown) ◄── clean_comment_body() ◄── Customer Portal Web UI
```

1. **`format_body_for_portal(text, fmt)`**: Translates Markdown headings, bullet lists, bold/italics, and backtick code blocks into portal-compliant HTML.
2. **`clean_comment_body(html)`**: Strips portal HTML tags and restores readable Markdown formatting for terminal display.

---

## 6. State Persistence & Concurrency

Operational state (followed cases, sync checkpoints, unread counts) is managed by `StateService` (`services/state.py`) in `state.yaml` under `$XDG_STATE_HOME/rh-support-cli/`.

### Thread-Safe Locking & Transaction Boundaries

* **Sidecar File Locking**: File operations are serialized using `fcntl.flock` on a `<state_file>.lock` sidecar file, guarding against concurrent writes across CLI, TUI, and background sync processes.
* **Full Read-Modify-Write Transactions**: Built-in mutation methods hold the instance `RLock` and sidecar `flock` for the entire transaction. A caller-managed `load_state() / save_state()` sequence is not automatically transactional.
* **Standalone Reads**: `load_state()` provides atomic snapshot semantics and does not require write access or sidecar locks.
* **Sidecar Invariants**: The persistent `.lock` sidecar file must remain in place on disk while clients run.
* **Atomic Replacement**: `save_state()` writes to a temporary file in the state directory and atomically replaces `state.yaml` via `os.replace()`.
* **Atomic Batch Mutations**:
  - `add_tracked_cases_batch()`: Commits multiple tracked case records in a single read-modify-write cycle.
  - `remove_followed_cases_batch()`: Removes multiple followed cases in a single transaction. Deduplicates case numbers, preserves owned records, and updates legacy migration structures atomically.
  - `batch_update_latest_known()`: Commits parallel sync results in one disk transaction.

### Account-Level Tracking Architecture

* **`CaseService.follow_account`**: Queries remote account cases via `list_cases(all_pages=True)` matching the status filter (default: `"open"`). Non-redundant cases are committed in a single atomic batch via `StateService.add_tracked_cases_batch()`. Dry-run calls pass `track_owned=False` to suppress automatic owned-case registration side-effects.
* **`CaseService.unfollow_account`**: Discovers followed cases matching the account number and optional status/severity filter locally via `list_followed_cases(fetch_live=False)`. Cases missing an account number in state are correlated using `CacheService.get_case_details(ttl=0)`. Expired cache entries remain usable for this local-only selection without remote network reads. Matching cases are removed in a single transaction via `StateService.remove_followed_cases_batch()`.

---

## 7. Local Caching & Cache Scoping

`CacheService` (`services/cache.py`) manages local disk caches under `$XDG_CACHE_HOME/rh-support-cli/`:

* **Namespace Isolation**: Private case lists and details are stored under `private/<context-hash>/`, where the hash binds the offline credential, SSO endpoint, and GraphQL URL. Changing credentials automatically isolates cached data.
* **Case Lists**: Cached under `lists/` using a deterministic SHA-256 key of sorted query parameters (`status`, `severity`, `account`, `owner`, `product`, `limit`). Controlled by `list_ttl` (default: 60s).
* **Case Details**: Cached under `cases/<case_number>.json`. Validated against the server using lightweight GraphQL probes (`check_case_last_modified`) before re-fetching full detail payloads.
* **Comment Coverage Contract**: Cached records store `_comment_coverage` indicating whether comments are `complete` or represent an `available_prefix`. Full requests and instant TUI views require complete coverage; partial prefixes cannot satisfy full-thread requests. Required continuation failures raise `IncompleteCaseHistoryError` before cache writes.
* **Bypassing Cache**: Passing `--no-cache` or setting `RH_NO_CACHE=1` bypasses cache reads and writes globally.

---

## 8. Terminal User Interface Architecture (Textual Framework)

The interactive dashboard (`tui/app.py`) is built with the Textual framework:

* **Dual-Layout Topologies**:
  - Horizontal Split (Default): Mailbox layout placing the case list on top and detail view below.
  - Vertical Split: Side-by-side layout with case list on the left and details on the right.
* **Dynamic View-Swapping**: When navigating cases, `#case-detail-container` clears active widgets via `container.query("*").remove()` and mounts a freshly compiled layout tree to prevent widget collisions.
* **Non-Blocking Background Workers**: All network requests run on worker threads via `self.run_worker(..., thread=True)`. Results are dispatched back to the UI thread using thread-safe callbacks. Active jobs display in the header status bar via `track_job()`.
* **Stale-While-Revalidate (SWR)**: Selecting a case immediately renders cached details from disk while a background worker validates remote freshness.
* **Status Bar Chips**: Header chips display real-time identity, active bookmark scope, and list freshness without triggering blocking network I/O.
* **Comment Composer Safety**:
  - The status dropdown preselects `determine_reply_status(current_status)`, defaulting to `"In Progress"` only if the case is waiting on customer action; otherwise it retains current status.
  - Submitting a comment checks whether the target status differs from current status; if unchanged, `status=None` is passed to avoid redundant API mutations.

### TUI Concurrency & Ownership Invariants

* **Detail Generations & Target Case IDs**: Selection and refresh callbacks capture a monotonically increasing detail generation and case ID. Callbacks verify that the target case remains selected before updating the UI thread, preventing stale background responses from overwriting newer selections.
* **Composer Draft Ownership**: Composer generations distinguish submitted drafts from active edits. Submitting holds the editor until confirmed success. Background refreshes do not overwrite unsent drafts.
* **Single-Flight Polling**: Polling checks the selected case against displayed metadata rather than cache TTL, avoiding duplicate concurrent background queries.
* **Read Failure Retention**: If a background read fails, the TUI displays a warning and retains the last complete view, attachments, and draft. Failed reads invalidate dwell timers and block read acknowledgement until revalidated.

---

## 9. Test Suite & Mock Architecture

The test suite runs fully offline without external network dependencies.

### Sandboxed Test Execution (`tests.support.SandboxSuite`)

The test runner wraps test discovery in a sandboxed environment:
* Uses isolated temporary directories for `HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, and `XDG_CACHE_HOME`.
* Installs a loopback-only network guard intercepting socket creation and DNS resolution, preventing accidental network leaks.
* Tests execute against a built-in mock server (`MockRedHatHandler`).

### Build Tools & Test Prerequisites

Provision `pip>=22.3`, `setuptools`, and `wheel` before running the test suite:
```bash
python -m pip install "pip>=22.3" setuptools wheel
```

### Running Tests and Linting

```bash
# Run unit test suite (buffered runner)
python -m tests

# Adjust test progress verbosity
python -m tests -v
python -m tests -q

# Run focused test modules
python -m tests tests.test_cli_comments
python -m tests tests.test_account_tracking
python -m tests tests.test_tui_actions

# Run Ruff linter
ruff check .

# Verify Ruff formatting
ruff format --check .

# Auto-format codebase
ruff format .
```

### Python 3.9 Compatibility Validation

The project supports Python 3.9+. To test in an isolated Python 3.9 environment:

```bash
# Using uv:
uv venv --python 3.9 .venv39 && source .venv39/bin/activate
pip install -e .
python -m tests

# Or using standard venv:
python3.9 -m venv .venv39 && source .venv39/bin/activate
pip install "pip>=22.3" setuptools wheel
pip install -e .
python -m tests
```

* **Type Annotation Rules**: Use `from __future__ import annotations`. Base class generic parameters (e.g. `ModalScreen[Optional[str]]`) and runtime expressions (`isinstance`) are evaluated at import time. On Python 3.9, PEP 604 union pipes (`|`) fail at runtime on generic aliases; use `typing.Optional` / `typing.Union` in base class subscripts.
* **Argcomplete Exclusion**: `setup.py` declares `argcomplete>=3.0.0,!=3.7.1` because release 3.7.1 introduced a Python 3.9 import error in its global activation script.

### Verified Dependency Floors

| Package | Minimum Supported Floor | Role |
| :--- | :--- | :--- |
| `textual` | `6.0.0` | Terminal user interface framework |
| `rich` | `13.3.3` | Terminal styling, panels, and tables |
| `markdown-it-py` | `2.2.0` | Markdown parsing engine |
| `argcomplete` | `3.0.0` (excluding `3.7.1`) | Bash auto-completion |

---

## 10. Refreshing the Bundled Product Catalog

The repository bundles an offline product catalog (`rh_support_lib/data/products.json`) for tab-completion and offline product lookups:

```bash
# Update catalog using active token:
./scripts/update_catalog.py

# Or pass an explicit token file:
./scripts/update_catalog.py --token-file /path/to/token.txt
```

---

## 11. Dependency Policy

`setup.py` declares explicit minimum version constraints (`install_requires`):

* Dependencies declare **lower bounds only** (no artificial upper pins) to allow flexible downstream packaging.
* Direct imports in code must always be declared in `setup.py`.
* Upgrades are validated against the test suite across all supported Python runtimes (3.9–3.14).
