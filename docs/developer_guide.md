# Red Hat Support Case CLI - Developer Guide

This guide is for developers working on the codebase, maintaining the CLI and library components, or extending its capabilities.

---

## 1. Project Architecture

The project is structured as a Python CLI wrapper around the Red Hat Support Case API. It is decoupled into library utilities, standalone commands, and an interactive Terminal User Interface (TUI).

```
/home/mh/rh-support-cli/
├── rh-support-cli.py       # Global CLI entry-point script
├── setup.py                # Package installation configuration
├── test_rh_support_cli.py  # Comprehensive end-to-end unit test suite
└── rh_support_lib/         # Core application library
    ├── api.py              # Red Hat SSO auth, OIDC token caching, GraphQL/REST HTTP client
    ├── config.py           # Configuration (bookmarks, templates, debug files)
    ├── constants.py        # Shared API endpoints, severity mapping, color codes
    ├── models.py           # Clean domain data models (Case, CaseComment, Attachment, CaseDetails)
    ├── templates.py        # YAML Jinja2 template processing engine
    ├── utils.py            # CLI text colorizing, smart comment header stripping
    ├── commands/           # Single-command CLI implementations
    │   ├── actions.py      # comment, attach, link, completion subcommands
    │   ├── apply_template.py # apply, list-templates subcommands
    │   ├── attachments.py  # attachment subcommand group (list, upload, download, delete, status)
    │   ├── create_case.py  # create subcommand
    │   ├── list_cases.py   # list subcommand (Rich Table based)
    │   ├── show_case.py    # show subcommand (Rich Panels/Markdown based)
    │   ├── tracking.py     # sync and updates subcommands, systemd unit generation
    │   └── watchers.py     # follow, unfollow, following subcommands (local state)
    ├── services/           # Decoupled domain service layer
    │   ├── attachments.py  # AttachmentService (v3 presigned upload, download, delete, status)
    │   ├── cases.py        # CaseService (GraphQL queries, mutations, case lookups)
    │   └── state.py        # StateService (Generic operational state, atomic YAML persistence)
    ├── synthetic_corpus/   # Local stateful mock portal and synthetic generator
    │   ├── generator.py    # Synthetic corpus generator
    │   └── server.py       # Stateful loopback mock HTTP and GraphQL server
    └── tui/                # Interactive Terminal User Interface (TUI)
        └── app.py          # Dashboard UI, focus containers, modals (Textual)
```

### Key Design Decoupling
*   **Library-First Domain Services**: Core domain logic is encapsulated within `rh_support_lib/services/` (`CaseService`, `AttachmentService`, and `StateService`). Both CLI subcommands (`commands/`) and the interactive dashboard (`tui/`) act as presentation-only consumers of normalized domain models (`models.py`), ensuring complete separation of concerns and zero API/state leakages into UI code.
*   **Generic State & Cache Decoupling**: Persistent operational metadata (e.g. followed/owned cases registry, read checkpoints, unread counts, sync checkpoints, pinned tickets, local tags) is managed via `StateService` (`state.yaml`) with atomic file writes and per-identity user scoping.
*   **Local Follow & Change Tracking**: Because remote server-side watchers are deprecated in the new CRM platform, case tracking is handled locally via `StateService`. Checkpoints track `status` and `last_read_comment_date`/`last_read_comment_id`. Parallel background syncing (`sync`) and review commands (`updates`, `show --mark-read`) enable fast differential analysis without server-side watcher dependencies.
*   **Modern API Backend**: `CaseService` and `AttachmentService` communicate natively with Red Hat's CRM platform via GraphQL (`https://graphql.redhat.com`) and the v3 Attachment Service.
*   **Presigned Direct Transfer Attachments**: Under the new CRM mode, large files up to 5 TB bypass intermediate API gateways via `AttachmentService`, using single PUT ($\le 5\text{ GB}$) or multipart parallel chunks (> $5\text{ GB}$) with automatic completion verification.
*   **No-Auth Execution Paths**: Commands that only inspect local resources (like `list-templates` and `completion`) are routed before OIDC token fetching in `main.py`, providing fast, completely offline execution.
The interactive dashboard is implemented using the **`Textual`** reactive framework. 

### Responsive Dual-Layout (Horizontal Mailbox Default vs Vertical)
The TUI supports two responsive viewing topologies:
1.  **Horizontal Split (`Screen` - Default)**: Mailbox style (`grid-rows: 2fr 3fr; grid-columns: 1fr`), positioning a full-width case table across the top and case details below. Ideal for terminal multiplexers (`tmux`/`screen`) and wide viewports.
2.  **Vertical Split (`Screen.layout-vertical`)**: Side-by-side (`grid-columns: 2fr 3fr`), ideal for standard terminal orientations.
Panes utilize compact zero-margin boundaries (`margin: 0; padding: 0 1;`) to maximize visible information density, with single-pane zoom (`f` / `focus_pane`) seamlessly expanding whichever pane is active across the entire viewport in both modes.

### Dynamic Container View-Swapping
To prevent standard widget-mounting or type-mismatch exceptions inside Textual, the right-hand case detail pane (`#case-detail-container`) uses a view-swapping architecture:
1.  On transition, the container's active views are cleared using `container.query("*").remove()`.
2.  A new flat list of static elements (Header, Details Table, Markdown Description, and scrollable Comment Panels) is compiled.
3.  The complete, compiled child list is passed to a new `Vertical(*widgets)` layout constructor and mounted directly onto `#case-detail-container`.

### Multi-Threaded Non-Blocking Background Workers
All slow network operations (fetching cases, getting details, posting comments, or executing templates) run on background threaded workers (`self.run_worker(lambda: fn(), thread=True)`). This prevents synchronous REST queries from blocking the main Textual event loop, maintaining a fluid, lag-free UI.

---

## 3. The End-to-End Mock Testing Suite (`test_rh_support_cli.py`)

To allow complete, offline end-to-end verification without real credentials, the test suite initiates a local Python `http.server.HTTPServer` mocking the Red Hat SSO and Case Management APIs.

### Test Coverage Highlights
*   **Authentication & Caching**: Tests mock token caching, token expiration recovery, and missing configuration failures.
*   **CLI Subcommands**: Validates CLI flags, attachment uploads, comments, and template variables.
*   **Headless TUI Testing**: Programmatically launches the real Textual TUI app inside an asynchronous `async with app.run_test()` context manager, compiles the CSS stylesheets, mounts widgets, and asserts that the mock HTTP queries succeed and render successfully.

To run the test suite:
```bash
.venv/bin/python test_rh_support_cli.py
```

To run linting and formatting validation:
```bash
ruff check .
ruff format --check .
```
