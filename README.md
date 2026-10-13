# Red Hat Support Case CLI (`rh-support-cli`)

A modern, developer-centric command-line interface and interactive Terminal User Interface (TUI) to manage, troubleshoot, and coordinate Red Hat support tickets directly from your shell.

---

## Overview & Core Capabilities

The `rh-support-cli` tool bridges the gap between local system operations and the official Red Hat Customer Portal, enabling platform engineers, SREs, and system administrators to manage support cases with zero friction:

* **Case Management**: Create, view, list, comment on, and close support tickets directly from the terminal.
* **Unified Search**: Search across your account's support cases and the Red Hat Knowledgebase (KCS) technical solutions and articles, with clickable browser links.
* **Large File Attachments**: Upload diagnostic bundles and log archives up to 5 TB with **automated multipart streaming** (<50 MB RAM usage) and live progress bars. No manual file splitting needed.
* **Local Case Tracking & Background Sync**: Follow tickets locally, track read checkpoints, and receive desktop notifications on new comments or status transitions via background timers.
* **Rich Text / Markdown Formatting**: Author comments and descriptions in natural Markdown; the CLI automatically formats them into portal-compliant rich text (HTML) to preserve line breaks and code formatting in the Customer Portal web UI.
* **Interactive Terminal User Interface (TUI)**: A responsive, split-pane dashboard (`tui`) featuring instant Stale-While-Revalidate (SWR) case rendering, auto-scrolling to unread comments, and full keyboard navigation.
* **Automation & Sizing Templates**: Pre-fill complex case details and enforce standard fields using Jinja2-compatible YAML templates and filter bookmarks.
* **Stateful Offline Mock Mode**: Built-in mock portal server (`--mock`) for zero-credential demos and offline scripting.

---

## 5-Minute Quick Start

### 1. Installation

Install the CLI and dependencies using `pip`:

```bash
# Install from source:
pip install .

# Or for development (editable mode):
pip install -e .
```

### 2. Authentication

Generate an Offline Token from the [Red Hat API Portal - Offline Token Generator](https://access.redhat.com/management/api) and export it in your shell:

```bash
export REDHAT_SUPPORT_OFFLINE_TOKEN="your_offline_token_here"
```

*(You can also save it permanently to `~/.config/rh-support-cli/token` with permissions `600`.)*

### 3. Essential Commands

```bash
# 1. List active support cases:
rh-support-cli list --status open

# 2. View a case, description, and full comment thread:
rh-support-cli show -c 12345678

# 3. Search cases and Knowledgebase (KCS) solutions:
rh-support-cli search "ingress cert expired" --type all

# 4. Attach a diagnostic archive (automatically handles files up to 5 TB):
rh-support-cli attach -c 12345678 -f /tmp/sosreport.tar.xz

# 5. Post a Markdown comment and update status:
rh-support-cli comment -c 12345678 -f update.md -s redhat

# 6. Launch the interactive split-pane dashboard:
rh-support-cli tui

# 7. Install regular background sync timer (runs every 15 min with desktop alerts):
rh-support-cli install-systemd --enable --notify
```
---

## Documentation Directory

Detailed guides, configuration specifications, and technical design documents are organized inside the [`docs/`](docs/) directory:

| Document | Audience | Contents |
| :--- | :--- | :--- |
| **[User Guide](docs/user_guide.md)** | Operators & Users | Complete command reference, configuration (`config.yaml`), bookmarks, templates, rich text formatting options, attachment handling, and TUI keyboard shortcuts. |
| **[Developer Guide](docs/developer_guide.md)** | Developers & Contributors | Codebase file structure, domain decoupling, GraphQL & Hydra API protocols, multipart streaming engine (`FilePartStream`), thread safety, caching, and testing. |
| **[Vision Guidelines](docs/vision.md)** | Strategic Compass | North Star mission statement, target personas, and the 5 immutable architectural design pillars governing the project. |

---

## Running the Test Suite

The project includes an extensive test suite simulating Red Hat SSO, CRM GraphQL, and v3 Attachment APIs with zero external dependencies:

```bash
# Run unit tests:
python3 test_rh_support_cli.py

# Run Ruff linter and code formatting validation:
ruff check .
ruff format --check .
```

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
