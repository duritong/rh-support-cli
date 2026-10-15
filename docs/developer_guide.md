# Red Hat Support Case CLI - Developer Guide

This guide details the codebase architecture, design decisions, API protocols, and internal subsystems for developers and contributors maintaining the CLI and library components.

---

## 1. Project Architecture & File Tree

The project is structured as a decoupled Python application where presentation layers (CLI and TUI) serve as thin wrappers over domain services and models:

```
rh-support-cli/
├── rh-support-cli.py           # Global CLI entry-point script
├── setup.py                    # Package installation configuration
├── test_rh_support_cli.py      # Comprehensive end-to-end unit test suite
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
    │   ├── list_cases.py       # list subcommand (Rich Table rendering)
    │   ├── search.py           # search subcommand (Cases and KCS Knowledgebase)
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

---

## 3. API Protocols & Backend Communication

HTTP debug logging recursively redacts credential fields, cookies, and presigned upload/download URLs in JSON and headers. Signed request URLs have their query values redacted. Streamed response bodies are never consumed for logging; only their status and headers are recorded. File logging retains complete redacted non-streamed responses, while console logging truncates long bodies. Non-JSON request bodies are omitted rather than inspecting file streams or opaque credential-bearing payloads.

Access-token cache entries carry an opaque SHA-256 fingerprint of the resolved offline credential, actual SSO endpoint, and OAuth client ID. A valid expiry alone is not sufficient for reuse: the fingerprint must match the current authentication context. Legacy entries without a binding are cache misses and are replaced on the next exchange. The existing cache filenames and credential precedence remain unchanged; the cache stores one context per file, not a profile registry.

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
* **Solr Filter Query (`fq`)**: Queries `https://access.redhat.com/hydra/rest/search/kcs?q=<term>` using Apache Solr parameters.
* **Language & Product Scoping**: Passes `fq=language:<lang>` and `fq=product:"<name>"` to target localized documentation and solutions.

---

## 4. Large Attachment Streaming & Multipart Engine

The attachment subsystem (`AttachmentService` in `rh_support_lib/services/attachments.py`) handles files from a few kilobytes up to 5 TB:

```
                            File Upload Request
                                     │
                                     ▼
                    File Size > MULTIPART_THRESHOLD (5 GB)?
                                     │
                    ┌────────────────┴────────────────┐
                    │ YES                             │ NO
                    ▼                                 ▼
         Initiate Multipart Upload           Initiate Single PUT
           (totalChunks calculated)           (presignedUrl returned)
                    │                                 │
                    ▼                                 ▼
         Stream 5 GB Parts via                Stream Entire File
           FilePartStream (8 MB blocks)         (FilePartStream)
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
* Implements `read(size)` reading in small 8 MB memory blocks (`block_size = 8 * 1024 * 1024`).
* Implements `__len__` returning the part's exact slice length, enabling `requests.put` to set the HTTP `Content-Length` header automatically.
* Closes file handles safely via context manager (`__enter__` / `__exit__`).
* Memory usage remains bounded under 50 MB regardless of total file size.

### Part Boundary Sizing
For a file of size $S$ divided into $N$ parts of chunk size $C$:
$$\text{start\_offset} = i \times C, \quad \text{part\_length} = \min(C, S - \text{start\_offset})$$
This ensures the final chunk accounts for all remainder bytes and eliminates file corruption.

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
* **Case Details**: Cached under `cases/<case_number>.json`. Validated against remote servers using lightweight GraphQL probes (`GQL_CHECK_CASE_LAST_MODIFIED`, ~250 bytes) checking `LastModifiedDate`, `Status`, and latest comment metadata before re-fetching full detail payloads.
* **Bypassing Cache**: Passing `--no-cache` or setting `RH_NO_CACHE=1` bypasses cache reads and writes globally.

---

## 8. TUI Architecture (Textual Framework)

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

---

## 9. Test Suite & Mock Architecture

The test suite (`test_rh_support_cli.py`) runs fully offline without requiring network connectivity or real credentials.

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
# Run unit test suite
python3 test_rh_support_cli.py

# Run Ruff linter
ruff check .

# Verify Ruff formatting
ruff format --check .

# Auto-format codebase
ruff format .

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
```
```
