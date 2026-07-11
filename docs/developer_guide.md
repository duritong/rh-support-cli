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
    ├── api.py              # Red Hat SSO auth, OIDC token caching, HTTP client
    ├── config.py           # Configuration (bookmarks, templates, debug files)
    ├── constants.py        # Shared API endpoints, severity mapping, color codes
    ├── templates.py        # YAML Jinja2 template processing engine
    ├── utils.py            # CLI text colorizing, smart comment header stripping
    ├── commands/           # Single-command CLI implementations
    │   ├── actions.py      # comment, attach, link, completion subcommands
    │   ├── create_case.py  # create subcommand
    │   ├── list_cases.py   # list subcommand (Rich Table based)
    │   └── show_case.py    # show subcommand (Rich Panels/Markdown based)
    └── tui/                # Interactive Terminal User Interface (TUI)
        └── app.py          # Dashboard UI, focus containers, modals (Textual)
```

### Key Design Decoupling
*   **Separation of Concerns**: Core operations like OIDC authentication, OIDC caching, and requests are isolated in `api.py`. Commands in `commands/` and the interactive interface in `tui/` act as separate UI consumers of these library routines.
*   **No-Auth Execution Paths**: Commands that only inspect local resources (like `list-templates` and `completion`) are routed before OIDC token fetching in `main.py`, providing fast, completely offline execution.

---

## 2. Terminal User Interface (TUI) Architecture

The interactive dashboard is implemented using the **`Textual`** reactive framework. 

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
