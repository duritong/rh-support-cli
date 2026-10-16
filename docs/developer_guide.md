# Red Hat Support Case CLI - Developer Guide

This guide details the codebase architecture, design decisions, API protocols, and internal subsystems for developers and contributors maintaining the CLI and library components.

---

## 1. Project Architecture & File Tree

The project is structured as a decoupled Python application where presentation layers (CLI and TUI) serve as thin wrappers over domain services and models:

```
rh-support-cli/
├── rh-support-cli.py           # Global CLI entry-point script
├── setup.py                    # Package installation configuration
├── tests/                     # Offline test package (excluded from installation)
│   ├── __init__.py            # Sandboxed discovery via load_tests
│   ├── __main__.py            # Canonical buffered test runner
│   ├── test_rh_support_cli.py # Comprehensive end-to-end tests
│   ├── test_*.py              # Focused regression tests
│   └── support/              # Shared sandbox and test helpers
├── scripts/
│   └── update_catalog.py       # Script to refresh repository products.json from API
└── rh_support_lib/             # Core application library
    ├── data/
    │   └── products.json       # Bundled catalog of Red Hat products for offline auto-completion
    ├── config.py               # YAML configuration loader (bookmarks, templates, cache)
    ├── constants.py            # Shared API URLs, picklist status mappings, ANSI colors
    ├── models.py               # Domain models (Case, CaseComment, Attachment, KCSSearchResult)
    ├── completers.py           # Dynamic shell auto-completers (products, statuses, bookmarks, templates)
    ├── templates.py            # YAML Jinja2 template processing engine
    ├── utils.py                # Formatting, rich-text conversion, ISO date helpers
    ├── commands/               # CLI presentation commands
    │   ├── actions.py          # comment, close, attach, link, completion
    │   ├── apply_template.py   # apply, list-templates
    │   ├── attachments.py      # attachment subcommands (list, upload, download, delete, status)
    │   ├── create_case.py      # create subcommand
    │   ├── doctor.py           # doctor diagnostic health check command
    │   ├── drafts.py           # drafts discovery, preview, and cleanup commands
    │   ├── show_case.py        # show subcommand (Rich Panels and Markdown paging)
    │   ├── systemd.py          # systemd and install-systemd unit installation/management
    │   ├── tracking.py         # sync and updates subcommands
    │   └── watchers.py         # follow, unfollow, following subcommands
    │   ├── attachments.py      # AttachmentService (v3 presigned upload, multipart, download)
    │   ├── cache.py            # CacheService (Atomic XDG disk cache, SWR, TTL management)
    │   ├── cases.py            # CaseService (GraphQL queries, mutations, case lookups)
    │   ├── search.py           # SearchService (Unified Case & Hydra KCS search coordinator)
    │   └── state.py            # StateService (Thread-safe state.yaml, atomic persistence)
    ├── synthetic_corpus/       # Local stateful mock portal and synthetic generator
    │   ├── generator.py        # Synthetic test corpus generator
    │   └── server.py           # Stateful loopback mock HTTP and GraphQL server
    └── tui/                    # Interactive Terminal User Interface (TUI)
        └── app.py              # Textual dashboard, view-swapping containers, modals
```

---

## 2. Core Architecture & Decoupling

* **Library-First Domain Services**: All business logic lives in `rh_support_lib/services/`. Neither CLI commands (`commands/`) nor the TUI (`tui/`) execute raw API requests or directly manipulate state files. They consume normalized domain models from `models.py`.
* **Presentation-Only Commands**: Each CLI command handles argument parsing, terminal output formatting (via `rich`), and delegates all operations to services.
* **Template Apply Contract**: `APPLY_FIELDS` is limited to the fields accepted by `CaseService.update_case`: status, severity, summary, and description. Unsupported directives and values that the service would ignore are rejected before mutation. Dry-run and execution use the same validated difference dictionary; execution forwards that dictionary without silently dropping fields.
* **Fail-Closed Template Rendering**: `TemplateEngine` raises `ValueError` on load/include/shape/render failures rather than returning partial data or terminating the host process. Jinja uses `StrictUndefined`; errors identify the field path. Include nesting is capped at 100, cycles are rejected, and recursive YAML data cannot reach mutation. CLI presenters translate errors to `Template Error` diagnostics and nonzero exits. Explicit optional-variable defaults remain supported.
* **No-Auth Execution Paths**: Operations that inspect only local resources (e.g. `list-templates`, `completion`, or `--local` search) bypass OIDC token verification for instant execution.
* **Structured Serialization**: All query commands provide pure JSON/YAML serialization via `format_structured_output` (`--json`, `--format yaml`), suppressing banners and ANSI sequences for clean pipe processing with tools like `jq`.
  Empty tracking queries serialize the same list schema as populated queries. Template metadata warnings/progress are emitted on stderr; recursive/nonfinite metadata is rejected before output. JSON serialization forbids NaN/Infinity. Apply plans/results use an explicit result dictionary and emit it only after a successful read/render/dry-run/update stage; failures do not print partial JSON/YAML.
* **Output Option Parsing**: Serialization uses the internal `output_format` destination; create/comment/close retain their content-format destination. Child options use `argparse.SUPPRESS` defaults so absent child flags cannot overwrite root values. Both attachment aliases register their nested options. JSON/simple-output/cache flags are resolved from parsed arguments, not raw argument-string scans that can mistake content values for flags.
* **Effective Configuration**: CLI/TUI presenters explicitly pass the loaded config to service construction, including nested search services and the single case service reused throughout creation. `CaseService(config=...)` configures the real cache TTLs without loading configuration implicitly in the library or changing cache identity. Comment/close and the TUI template selector use the same configured template directory. Completers consult their parsed `--config-file` argument locally. Explicit state-file resolution and confirmation/output policies are unchanged.
* **Configuration Validation**: `load_config` raises safe `ConfigError` exceptions for selected-path/read/YAML/known-schema failures, while only an absent optional default returns `{}`. Numeric cache strings normalize to integers; finite nonnegative poll intervals can be fractional. `resolve_bookmarks` centralizes validation and merging before client or mock-backend startup. Presenters preserve CLI default-overlay and TUI replace/clear behavior. Mock activation uses parsed arguments after validation, never content-string scans. Unknown future configuration keys remain allowed.
* **Editor Approval Boundary**: `edit_file` executes the shlex-parsed editor argv without a shell. `EditorError` carries a safe reason and recovery path; neither it nor `open_editor` deletes failed drafts. CLI presenters reject empty content uniformly and preserve generated files on editing/read/submission failure. Core creation/comment methods also reject empty input before entity lookups. Leading-header stripping remains unchanged.

* **Creation Presenter**: `commands/create_case.py` delegates mutation and attachment preflight to `operations.create_case_with_attachments`. Full success returns a receipt; `PartialCaseCreationError.receipt` records confirmed creation plus every upload outcome, including cancellation. The presenter emits exactly one structured receipt on success/partial success, exits nonzero for partial success, and routes interactive/progress output to stderr. Serialization uses `args.json` / `args.output_format`, never the content `args.format`. Only explicit `create --yes` bypasses confirmation, after required flag/template values are checked without interactive fallback. Numeric YAML versions and integer severity codes 1-4 are normalized to strings before that check. Generated descriptions are preserved on creation failure or unknown outcome, but cleaned after confirmed creation even on attachment failure; cleanup warnings describe them as submitted copies. Creation validation currently uses untyped `ValueError`, so the presenter supplies fixed actionable validation guidance rather than exposing potentially remote exception text. A dedicated trusted validation type would be required to expose specific core failure reasons safely. Focused offline coverage lives in `tests/test_creation_cli.py` and `tests/test_creation_operations.py`.

---

## 3. API Protocols & Backend Communication

`CaseService.add_comment` returns a `CaseComment` on success. A confirmed post followed by an unconfirmed status change or cache invalidation raises `PartialCommentError`, carrying `comment`, `case_number`, `requested_status`, `status_confirmed`, and `cache_invalidation_failed`. The case cache is invalidated even when the status stage fails; no mutation is retried. A malformed confirmation raises `CommentOutcomeUnknownError` before any status mutation. CLI comment/close presenters serialize receipts independently of content format and exit nonzero for partial outcomes. TUI completion clears only the submitted body, preserves newer drafts, and displays recovery guidance. Focused coverage lives in `tests/test_comment_outcomes.py` and `tests/test_comment_presenters.py`.

HTTP debug logging recursively redacts credential fields, cookies, and presigned upload/download URLs in JSON and headers. Signed request URLs have their query values redacted. Streamed response bodies are never consumed for logging; only their status and headers are recorded. File logging retains complete redacted non-streamed responses, while console logging truncates long bodies. Non-JSON request bodies are omitted rather than inspecting file streams or opaque credential-bearing payloads.

Access-token cache entries carry an opaque SHA-256 fingerprint of the resolved offline credential, actual SSO endpoint, and OAuth client ID. A valid expiry alone is not sufficient for reuse: the fingerprint must match the current authentication context. Legacy entries without a binding are cache misses and are replaced on the next exchange. The existing cache filenames and credential precedence remain unchanged; the cache stores one context per file, not a profile registry.

Each `RedHatAPIClient` serializes credential pinning/acquisition/refresh with an `RLock`. Requests capture a token generation, so late concurrent 401s reuse an already refreshed generation even if the issuer returns the same token string. Only one retry is made, discarded 401 responses are closed, and caller headers are copied. Token-cache publication atomically replaces a complete 0600 temporary file in the 0700 cache directory, preventing mixed credential/token JSON from concurrent writers. Separate clients/processes may still independently exchange tokens.

Authentication raises sanitized `AuthenticationError` rather than printing SSO responses or terminating the host process; the CLI owns nonzero presentation and TUI workers handle ordinary exceptions. Explicitly selected empty/unreadable credential sources do not fall back to another identity. Before sending a request, the client captures offsets of seekable `data`/multipart streams. A 401 retry rewinds these offsets; generators/nonseekable bodies raise `RequestReplayError` without a second send or token refresh. Body contents are never buffered for replay. Callers remain responsible for keeping source content unchanged between attempts.

TUI rendering and local tracking/layout actions use local-only identity resolution, retaining the service's last resolved JWT identity while an access-token refresh is in flight. They must not trigger SSO from the UI thread. A known username claim in the pinned offline credential can also identify local state without authentication.

The CLI communicates with three backend endpoints:

| Service | Protocol | Default URL | Purpose |
| :--- | :--- | :--- | :--- |
| **CRM GraphQL API** | GraphQL over HTTP POST | `https://graphql.redhat.com` | Case listing, case details, comments, creation, lookups |
| **Attachment Service** | REST v3 / S3 Presigned URLs | `https://api.access.redhat.com/support` | Presigned single/multipart upload, download, delete |
| **Hydra Search API** | REST over HTTP GET | `https://access.redhat.com/hydra/rest` | Knowledgebase (KCS) solutions and technical articles |

### A. GraphQL CRM Protocol
* **Lookup Query Validation**: Creation lookups request up to two records per entity and reject missing, malformed, or ambiguous results. There are no production mock-ID defaults. An explicit account is independently resolved and must match the contact's account; otherwise the contact's account is used. Identity must come from an explicit SSO username/override or a username claim, never the local OS user or an opaque subject ID. The optional account branch uses standard GraphQL `@include` rather than querying a null account number.
* **Product Resolution Boundary**: Lookup still filters by name only and accepts a result only when its returned name/version match the request exactly and uniquely. No version-specific filter is guessed. Production filter/input schema verification is outstanding; ambiguous or mismatched versions fail before mutation. The offline unused-variable test is not full production-schema validation. `accountNumber` is consumed as a routing input, not forwarded in create fields.
* **Relay Cursor Pagination**: Queries use standardized Relay connection specifications (`edges { node, cursor }`, `pageInfo { hasNextPage, endCursor }`).
  * `CaseService.get_case_details`: Automatically exhausts comment pages up to limits using `endCursor`.
  * `CaseService.list_cases`: Paginates through ticket lists in configurable batch sizes (`first: 100`) when `--all` is specified.
* **Child Relationship Constraints**: On Red Hat's Salesforce GraphQL UIAPI, child relationship queries (such as `CaseComments__r`) do not support cursor pagination with `after`. Secondary comment batches fall back to root queries with `where: { Case__c: { eq: <id> } }` guarded by error recovery.

### B. Hydra KCS Search API
* **Response Normalization**: Search accepts Solr `response.docs`, top-level `docs`, or top-level document lists. An explicitly empty nested list is authoritative. Transport, malformed-container, and malformed-document failures raise `ReadError` rather than becoming empty results. Nonpositive limits return empty results without requests; remote/local merging supplements only successful remote queries, never disguises failures.
* **Solr Filter Query (`fq`)**: Queries `https://access.redhat.com/hydra/rest/search/kcs?q=<term>` using Apache Solr parameters.
* **Language & Product Scoping**: Passes `fq=language:<lang>` and `fq=product:"<name>"` to target localized documentation and solutions.

---

## 4. Large Attachment Streaming & Multipart Engine

The attachment subsystem (`AttachmentService` in `rh_support_lib/services/attachments.py`) handles files from a few kilobytes up to 5 TB:

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
To prevent `MemoryError` exceptions when handling multi-gigabyte files, `FilePartStream` streams slices of files without reading entire chunks into RAM:
* Positive-sized `read(size)` calls are bounded by `block_size` (default 8 MiB), including Requests' normal transport reads. Explicit `read(-1)`/`read()` retains file-like read-all semantics and is not memory-bounded.
* Implements `__len__` returning the part's exact slice length, enabling `requests.put` to set the HTTP `Content-Length` header automatically.
* Closes file handles safely via context manager (`__enter__` / `__exit__`).
* Normal transfer reads are independent of total file size; no measured process-wide peak-memory bound is asserted.

### Part Boundary Sizing
For a file of size $S$ divided into $N$ parts of chunk size $C$:
$$\text{start\_offset} = i \times C, \quad \text{part\_length} = \min(C, S - \text{start\_offset})$$
This ensures the final chunk accounts for all remainder bytes and eliminates file corruption.

Upload initiation requires a supported strategy and nonempty attachment ID. Multipart manifests are validated before any PUT: positive unique contiguous part numbers, URLs, and exact byte coverage. Shuffled valid manifests are sorted by part number before assigning slices. Each PUT must consume its entire expected slice, the last slice must reach source EOF, and multipart ETags must be present before completion. Usable multipart sessions are aborted on validation/transfer failure or interruption. These checks do not replace cryptographic integrity checks and cannot detect same-size concurrent edits. Existing small-file multipart sizing and ETag conventions are retained pending remote-contract verification.

### Abort Lifecycle on Interruption
If an upload is aborted, cancelled (`Ctrl+C`), or encounters a connection drop during multipart transfer, `AttachmentService.upload_attachment` traps the exception and calls `DELETE /v3/cases/attachments/upload/abort` to release storage session resources on the server.

### Safe Download Publication
Remote filenames must be single safe path components. Downloads stream into a private temporary file in the destination directory, validate an unencoded response's declared length, flush it, then publish it with `os.link`. This requires a filesystem supporting hard links and atomically refuses destination collisions, including a file or symlink created during the transfer. The response is closed and the temporary file removed on success, failure, callback exceptions, and interruption. No overwrite or copy-based fallback is used.

---

## 5. Rich Text & Comment Formatting Engine

### Salesforce `Body__c` Storage Architecture
Red Hat's CRM stores comment bodies and case descriptions as **Rich Text (HTML)**. The web portal renders this field using `innerHTML` without `white-space: pre-wrap`. Raw newlines (`\n`) in plain text collapse into whitespace in the browser.

### Bidirectional Conversion Pipeline
```
CLI / TUI Input (Markdown) ──► format_body_for_portal() ──► CRM HTML (Body__c)
                                                                    │
Terminal Display (Markdown) ◄── HTMLToMarkdownParser ◄──────────────┘
```

1. **Outbound Conversion (`format_body_for_portal` in `rh_support_lib/utils.py`)**:
   * `markdown` *(default)*: Uses `markdown_it` with GFM and soft breaks (`breaks: True`, `html: True`):
     * Single newlines (`\n`) become `<br />`.
     * Paragraph breaks (`\n\n`) become `<p>...</p>`.
     * Fenced code blocks become `<pre><code>...</code></pre>`.
     * Inline code becomes `<code>...</code>`.
     * Angle brackets outside code blocks are escaped safely.
   * `plain`: Escapes HTML entities and wraps text in `<p>` and `<br />` without Markdown syntax parsing.
   * `html`: Verbatim HTML passthrough.
   * `raw`: Unformatted string passthrough.
2. **Inbound Conversion (`clean_comment_body` / `HTMLToMarkdownParser`)**:
   * Scans for HTML elements (`<p>`, `<pre>`, `<code>`, `<a>`, `<ul>`, etc.).
   * Translates HTML back into clean terminal Markdown for Rich and Textual rendering.
   * Decodes at most two outer transport-encoding layers, stopping as soon as HTML is recognized. Text entities inside actual HTML are decoded by the parser, not reparsed as markup, preserving literal XML/HTML examples and entity spellings in code.
   * Normalizes extra prose spacing without collapsing blank lines inside preformatted code blocks. Entirely escaped HTML without format metadata remains heuristically interpreted as legacy CRM HTML.

---

## 6. State Persistence & Thread Safety

Operational state (followed cases, sync checkpoints, unread counts, pinned cases) is managed by `StateService` (`rh_support_lib/services/state.py`) in `state.yaml` under `$XDG_STATE_HOME/rh-support-cli/`.

### Thread-Safe Re-Entrant Locking
Each built-in mutation holds its instance `RLock` for the complete read-modify-write transaction. A persistent `<state_file>.lock` sidecar is protected with `fcntl.flock`, serializing separate service instances and CLI/TUI/background-sync processes across atomic YAML replacement. The empty sidecar must not be removed while clients may be running. Same-instance nested calls reuse the held lock; lock-acquisition failures prevent mutation. Legacy migration also locks and rechecks the destination before copying, retaining its best-effort legacy fallback if migration is unavailable.

Standalone reads retain atomic-snapshot semantics and do not create directories or require a writable sidecar. A caller-managed `load_state()` / edit / `save_state()` sequence is not automatically transactional; use the built-in mutation methods. Advisory locking cannot protect against older clients or external writers that do not use the sidecar. Avoid nesting transactions across different instances on one thread.

To prevent lost updates during concurrent operations, `StateService` uses a re-entrant lock:
```python
self._lock = threading.RLock()
```
All state reads (`load_state`) and state writes (`save_state`) acquire `self._lock`.

### Atomic File Writes
`save_state` prevents partial writes or file corruption using atomic replacement:
1. Writes state to a temporary file in the target directory using `tempfile.mkstemp(dir=dir_name, prefix="state_", suffix=".tmp")`.
2. Flushes and closes the file descriptor.
3. Atomically replaces the target `state.yaml` using `os.replace(temp_path, self.state_file)`.

### Atomic Batch Synchronization (`batch_update_latest_known`)
When `sync_tracked_cases` checks multiple cases in parallel using `ThreadPoolExecutor`:
* Worker threads collect update dictionaries in memory without touching `state.yaml` concurrently.
* The main thread commits all case updates in a single atomic `batch_update_latest_known` operation, eliminating multi-threaded file contention.

---

## 7. Thread-Safe Local Caching & SWR Layer

Encapsulated in `CacheService` (`rh_support_lib/services/cache.py`) under `$XDG_CACHE_HOME/rh-support-cli/`:

Private lists and case records live under `private/<opaque-context-hash>/{lists,cases}/`. Context combines a locally resolved credential, the actual SSO endpoint, and the effective GraphQL URL; legacy access-token clients use their supplied token and backend. Clients pin the offline credential used for authentication and cache selection together. Access-token refresh does not change the offline-credential namespace. No credentials are stored in directory names. Legacy unscoped private records are ignored, never migrated automatically. Case identifiers cannot traverse namespace directories.

`CacheService(scope=...)` requires an explicit nonempty scope to enable persistence. `CaseService` derives it without network access; injected caches must match the client/backend context. Unknown clients without a string-valued `get_cache_scope(backend)` contract have persistence disabled. Local search requires a scope and uses only locally available identity information for tracking annotations, never token exchange. The non-private product catalog remains in the shared `catalog/` directory for offline completion and the catalog updater.

* **Case Lists**: Saved under `lists/` using deterministic SHA256 key hashing computed from sorted query parameters (`status`, `severity`, `account`, `owner`, `limit`). Controlled by `list_ttl` (default: 60s).
* **Filter Fidelity**: List keys also include product and requested page size. CLI/TUI following paths forward the same account/status/severity/owner/product constraints; `CaseService` applies these after collecting followed cases. Local search and remote/local merging enforce owner as well as other constraints. Combined search forwards case-only filters to case search, never to KCS. Offline followed results with unavailable metadata cannot satisfy filters on that metadata.
* **Case Details**: Cached under `cases/<case_number>.json`. Validated against remote servers using lightweight GraphQL probes (`GQL_CHECK_CASE_LAST_MODIFIED`, ~250 bytes) checking `LastModifiedDate`, `Status`, and latest comment metadata before re-fetching full detail payloads.
* **Comment Coverage**: Private records include `_comment_coverage` with `complete` and `available_prefix`. Only an explicit terminal page without truncation establishes completeness. Bounded requests can reuse a sufficient validated prefix; full requests and instant TUI rendering require complete coverage. Legacy or malformed coverage is a cache miss. Limits apply to first/subsequent batches and cache hits without mutating cached objects. Required failed/malformed continuation pages raise `IncompleteCaseHistoryError` before cache writes; normalization is transactional per page so fallback does not duplicate partially parsed data. Latest-N editor context retrieves the complete thread before selecting its final N comments in server order.
* **Bypassing Cache**: Passing `--no-cache` or setting `RH_NO_CACHE=1` bypasses cache reads and writes globally.

---

## 8. TUI Architecture (Textual Framework)

KCS result selection stops its table event at the modal. The application's case-selection handler is scoped to `#case-table`, so other result tables cannot change the active case or initiate case-detail reads.

KCS searches run in modal-owned thread workers. Results are dispatched onto the UI thread and accepted only for the mounted modal's current search generation; later queries and modal removal invalidate older completions.

Initial queue loads, explicit refreshes, and background polling share the same case-list reader and captured search query. Polling cannot replace a keyword search with the ordinary queue; query changes invalidate in-flight results through list-request ownership.

`SearchService.search_cases(following=True)` searches the filtered followed-case set by number/summary before applying the result limit, rather than filtering a truncated general search. Local-only calls use tracked metadata and local identity without live case reads. TUI search loads and polls both forward this membership constraint.

Comment composer status choices/defaults use the matching loaded case details, not queue membership. Directly jumped cases outside the active filters therefore retain the same reply-status policy, including keeping closed cases closed.

Deferred KCS-link insertion requires matching displayed/selected case details and an open composer, and captures the case plus draft generation. Rejected or superseded insertions neither change hidden/new drafts nor announce success.

KCS table metadata and query-bearing messages use literal Rich `Text` rather than markup strings, preserving bracketed diagnostics and preventing externally supplied titles/products from being parsed as terminal markup.

KCS result rows use generation-local positional keys rather than remote URLs. Duplicate URLs remain individually selectable with their own titles; missing/invalid cursor positions select nothing rather than falling back to the first result.

The KCS modal overrides the workspace screen grid with a centered vertical layout. Its bounded container and flexible result-table height keep results and action buttons visible at standard 80x24 and 120x40 terminal sizes; mounted tests resize the terminal and click the visible controls.

`tests/test_tui_bindings.py` maintains an explicit key/action contract independent of `BINDINGS`, presses every application key/alias and scroll binding in mounted apps, verifies real follow persistence/badges and pane/layout effects, and types printable shortcuts into editor/modal inputs without invoking actions. New or removed bindings require a deliberate coverage update. `Ctrl+X` has priority so the focused editor cannot consume the composer-cancel shortcut; dirty drafts still use Save/Discard/Cancel.

The interactive dashboard (`rh_support_lib/tui/app.py`) is implemented using Textual:

* **Dual-Layout Topologies**:
  * **Horizontal Split (`Screen` - Default)**: Mailbox layout (`grid-rows: 2fr 3fr`), placing the case list on top and details below.
  * **Vertical Split (`Screen.layout-vertical`)**: Side-by-side split (`grid-columns: 2fr 3fr`).
* **Dynamic Container View-Swapping**:
  To prevent widget mounting collisions when navigating cases, `#case-detail-container` clears active views via `container.query("*").remove()` and mounts a freshly compiled `Vertical(*widgets)` layout tree.
* **Non-Blocking Background Workers**:
  Network operations run on background threads via `self.run_worker(lambda: fn(), thread=True)`. UI updates are dispatched back to the main thread loop using thread-safe callbacks. Active tasks are visually reported in the header bar via `track_job(description)`.
* **Operation Targets and Detail Generations**:
  Comment, upload, template, and download actions capture their case identifier before scheduling a worker. Selection and refresh requests carry a monotonically increasing detail generation. Loading, result, and error callbacks check the target and generation on the UI thread before changing the detail pane, including when navigating away and back to the same case. Mutation refreshes and errors only affect the pane while its original target remains selected.
* **Draft Ownership**:
  Composer generations distinguish submitted drafts from newer drafts, including navigation back to the same case. Posting retains the editor until confirmed success and guards duplicate Post events before worker scheduling. Completion releases submission bookkeeping regardless of selection; it clears only the unchanged submitted draft. Refresh failures after posting do not restore it as unsent. Draft persistence writes unique private files in a worker, then performs UI cleanup/navigation/quit on the UI thread only if the saved draft still owns the composer. No worker sleeps or performs widget mutations during file I/O.
* **Stale-While-Revalidate (SWR)**:
  When a row is selected, cached details render immediately (0 ms) from disk cache while a background worker validates remote freshness.
* **Polling Ownership and Freshness**:
  List requests capture filters, selection, and request generations on the UI thread before I/O. Only current results/loading/errors may update the UI, and an empty result is a valid list replacement. Background polls remain single-flight through list, probe, and detail I/O. Selected details are compared with displayed metadata, not cache TTL; unchanged list metadata triggers the existing lightweight case/comment probe, including when the selected case leaves the active filter. Changed or unknown metadata permits a guarded full fetch. Draft contents remain independent of these refreshes.
* **Read Failure Contract**:
  Required read failures use safe library `ReadError` exceptions. CLI queries fail nonzero instead of inventing empty/not-found results. The TUI warns and retains its last complete view, attachments, list, and draft; stale warnings are discarded. Detail actions require the selected and displayed identities to match. Failed reads invalidate dwell and block manual read marking until successful retrieval/revalidation. Ownership re-registration preserves existing read checkpoints. Sync persists only successfully read cases and raises `PartialSyncError` with an internal report if others fail; failed cases and read checkpoints remain unchanged.
* **Actions and Read Dwell**:
  Template/bookmark modals leave absent selections to Textual's constructor default and match option values, avoiding version-specific sentinel names. Bookmark and focus shortcuts are non-priority bindings, preserving printable editor input. Order toggling rerenders the existing details locally. A single read-dwell timer is tied to a read generation and the exact displayed details object; navigation, detail refresh, rerender, and error replacement invalidate it, including already queued callbacks. A list-only refresh leaves the displayed version's dwell intact. Regression tests mount the controls, press actual keys, and execute controlled timer callbacks; the historical layout coroutine is now actually run.

---

## 9. Test Suite & Mock Architecture

All tests live in the `tests/` package: focused regressions use `tests/test_*.py`, and the main end-to-end module is `tests/test_rh_support_cli.py`. The package's `tests/__init__.py` exposes a `load_tests` hook that discovers all tests with the repository as the top-level directory. There is no explicit focused-module list in the main test module to maintain.

The canonical entry point wraps collected cases in `tests.support.SandboxSuite`. It uses a temporary working directory as well as a temporary HOME/XDG/TMP tree, clears credential/path/proxy/session overrides, and blocks non-loopback DNS and socket connections. Relative-path fixtures are created in the temporary working directory, not the repository root. Python CLI subprocesses inherit the network guard through a test-only `sitecustomize` path; normal application imports do not activate it. Real Requests serialization with a mocked transport remains testable. Spawn/forkserver children must finish before cleanup. This is serial process-global test isolation, not an OS sandbox for native/non-Python networking. Standard package discovery with `python -m unittest discover -b` also activates the sandbox through the package hook. For focused modules or classes, use the canonical runner rather than unguarded focused unittest invocations that bypass that hook. The entire `tests` package, including `tests/support/`, is excluded from installed packages.

The test suite runs fully offline without requiring network connectivity or real credentials.

### Mock Server (`MockRedHatHandler`)
Starts a local loopback `http.server.HTTPServer` mocking:
* Red Hat SSO OIDC token exchange (`/auth/token`).
* CRM GraphQL UIAPI (`/graphql`) with query inspection and error simulation.
* Attachment Service v3 (`/v3/cases/attachments/upload`, `/upload/complete`, `/upload/abort`, `/downloadFile`).
* Hydra KCS REST search API (`/search/kcs`) supporting language codes and documentation results.

### Output Assertions and Completion Fixtures

Human-readable Rich output is compared using `Text.from_ansi(stdout).plain` so ANSI style transitions do not split expected labels. Structured-output tests continue to parse the original stdout as JSON or YAML.

The direct dynamic-completer test temporarily overrides HOME, cache/state locations, and explicit configuration/template/state paths with its own fixtures, restoring the environment afterward. It must not depend on the developer's inherited XDG or `RH_SUPPORT_*` paths.

### Running Tests and Linting

```bash
# Run unit test suite (passing-test output is buffered)
python -m tests

# Adjust unittest progress verbosity
python -m tests -v
python -m tests -q

# Run a focused module or class inside the same buffered sandbox
python -m tests tests.test_pagination
python -m tests tests.test_pagination.PaginationTests

# Standard package discovery (sandboxed by the package load_tests hook)
python -m unittest discover -b

# Run Ruff linter
ruff check .

# Verify Ruff formatting
ruff format --check .

# Auto-format codebase
ruff format .
```

The canonical runner buffers test stdout and stderr by default, including focused runs. Passing runs show only unittest progress and the final summary; failures, errors, and failing subtests retain tracebacks and their captured output. Inside the sandbox, asyncio logging writes directly to unittest's active stderr buffer, bypassing Textual's headless forwarding to the original terminal stream. This captures slow-callback warnings without disabling asyncio debug checks. Unbuffered runners retain normal stderr logging; the original asyncio handlers and propagation setting are restored afterward. Use `python -m tests -v` for test names or `python -m tests -q` for a quiet summary.

### Local Python 3.9 Validation

Use an actual Python 3.9 runtime, not just a syntax check under a newer interpreter. With `uv` installed, create a separate environment without replacing the project's normal virtualenv:

```bash
uv python install 3.9
uv venv --python 3.9 /tmp/rh-support-py39
uv pip install --python /tmp/rh-support-py39/bin/python -e .
/tmp/rh-support-py39/bin/python -B -m tests
```

Runtime and dependency installation require network access; the canonical test command activates the same offline sandbox on every supported Python version. Allow at least ten minutes for the full suite on slower runtimes. Keep the environment until validation is complete so failures can be reproduced with the same resolved dependencies.

TUI tests that construct modal widgets must run inside an active event loop, using `unittest.IsolatedAsyncioTestCase` and an async test method. On Python 3.9, asyncio locks bind to a loop during construction. Callbacks passed to Textual must also have introspectable Python signatures; wrap built-ins such as `threading.get_ident` in a Python function or lambda when testing UI-thread dispatch.

---

## 10. Refreshing the Bundled Product Catalog

The repository includes a bundled product catalog (`rh_support_lib/data/products.json`) used for offline shell auto-completion and default product lookups without requiring a network call.

To refresh this catalog with the latest active products from Red Hat's GraphQL API:

```bash
# Run with active environment variable or stored token:
./scripts/update_catalog.py

# Or pass a specific token file:
./scripts/update_catalog.py --token-file /path/to/token.txt
```

The script queries all active products across all pages from Red Hat's GraphQL API, saves the structured output into `rh_support_lib/data/products.json`, and updates your local user cache at `~/.cache/rh-support-cli/catalog/products.json`.

After running the script, review and commit the updated data file:
```bash
git add rh_support_lib/data/products.json
git commit -m "Update Red Hat product catalog"

---

## 11. Dependency Policy and Minimum Supported Versions

`setup.py` declares explicit evidence-based minimum versions (`install_requires`) to protect against contract drift and breaking changes on supported Python versions (>=3.9):

| Dependency | Minimum Floor | Purpose & Evidence |
| :--- | :--- | :--- |
| `requests` | `>=2.25.0` | HTTP client with connection pooling, Python 3.9 compatibility, and urllib3 1.26+ support. |
| `requests-toolbelt` | `>=0.9.1` | Multipart streaming encoder and adapter primitives. |
| `pyyaml` | `>=5.4.1` | YAML loading with `yaml.safe_load` security mitigations. |
| `jinja2` | `>=3.0.0` | Jinja2 template engine with Python 3.9 context isolation and typing support. |
| `dateparser` | `>=1.0.0` | Date and timezone parsing for comment timestamps and checkpoints. |
| `argcomplete` | `>=1.12.0` | Shell tab-completion supporting `_ARGCOMPLETE_STDOUT_FILENAME` and vertical-tab IPC. |
| `rich` | `>=12.0.0` | Terminal rendering, `Table.grid`, `Text.rstrip()`, `ROUNDED` box styling, and pagination. |
| `textual` | `>=0.40.0` | TUI framework supporting `ModalScreen`, event decorators, and DataTable operations. |
| `markdown-it-py` | `>=2.0.0` | Markdown rendering engine supporting `"gfm-like"` preset with soft breaks. |

### Upgrade & Pinning Policy
* Library dependencies declare **lower bounds only** (no artificial upper pins) so consumers and package distributors can resolve dependency trees without conflicting constraints.
* Direct imports in code must always be declared in `install_requires` rather than relied upon as transitive dependencies.
* Dependency upgrades are validated by running the canonical offline test suite (`python -m tests -q`) across all supported Python runtimes.
```
