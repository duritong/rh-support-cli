# Red Hat Support Case CLI - User Guide

A robust, modern command-line interface and interactive Terminal User Interface (TUI) to browse, create, and manage support cases against the Red Hat Case API.

---

## 1. Installation & Running

### Developer Editable Mode (Recommended)
This links your repository source code directly to the command binary. Any local modifications are instantly live when you run the command.
```bash
pip install -e .
```

### Running Directly from Script
You can also run the global entry script directly:
```bash
python3 rh-support-cli.py <subcommand>
```

---

## 2. Retrieving Your Red Hat Offline Token

To authenticate with the Red Hat Support Case API, you must obtain a **Red Hat Offline Token**. This token acts as a long-lived credential that the CLI exchanges automatically for short-lived OIDC access tokens.

### How to generate your token:
1.  Navigate to the official Red Hat API Portal: **[Red Hat Customer Portal - API Offline Token Generator](https://access.redhat.com/management/api)**.
2.  Log in using your standard Red Hat Customer or Partner credentials.
3.  Click **"Generate Token"** (or view your active token).
4.  Copy the generated token string.

### Storing your token:
The CLI looks for this token in three places (in order of priority):
1.  **Environment Variable**: Export the token globally in your shell session:
    ```bash
    export REDHAT_SUPPORT_OFFLINE_TOKEN="your_token_string"
    ```
2.  **Global Token File**: Store the token string inside your configuration directory at:
    ```bash
    ~/.config/rh-support-cli/token
    ```
3.  **Command-Line Prompt**: If no token is detected, the CLI will interactively prompt you to paste it on startup.

*To minimize round-trips and avoid re-authenticating on every run, active OIDC tokens are automatically cached locally inside `~/.cache/rh-support-cli/token_cache.json`.*

---

## 3. Configuration & Bookmarks

Configuration is stored in your user configuration folder: `~/.config/rh-support-cli/config.yaml`.

### Example `config.yaml`
```yaml
# Set your default template name for case creation or TUI pre-selection
default_create_template: base-template

# Set your default bookmark filters on case list startup
default_bookmark: ocp-team

# Optionally: Set your background debug file for logging full redacted HTTP sessions to analyze API responses
debug_file: ~/.config/rh-support-cli/debug_log.txt

# Group custom filter bookmarks
bookmarks:
  ocp-team:
    status: ["Waiting on Red Hat", "Waiting on Customer"]
    severity: ["High", "Urgent"]
    product: "OpenShift Container Platform"
```

---

## 4. Case Sizing Templates

Templates are Jinja2-compatible YAML files located inside `~/.config/rh-support-cli/templates/`. They allow pre-filling case values during creation (`create`) or enforcing compliance/updating fields post-creation (`apply`).

### Template Metadata and Underscore (`_`) Fields
Any keys in a template file starting with an underscore `_` are treated as **private metadata** for the template itself. They are ignored when rendering, compiling, or sending payloads to the Red Hat API, keeping your case comments/descriptions safe.

#### Example `templates/prod-standard.yaml`:
```yaml
_template_description: "Standard production severity and status settings"
severity: "2 (High)"
status: "Waiting on Customer Action Required"
```

### Comment Templates
You can also use templates inside the `comment` subcommand to pre-fill comment bodies and automatically change a case's status (e.g. closing a case, or requesting an escalation).

Comment templates can define:
*   `comment` (or `body` / `commentBody`): The rendered Jinja2-compatible comment text.
*   `status`: The case status (e.g., `closed`, `redhat`, `customer`) to update the case to after adding the comment.

#### Example `templates/close_case.yaml`:
```yaml
_template_description: "Standard request to close a support case"
status: "closed"
comment: |
  Hi Support,

  We have verified that the proposed solution works perfectly for our {{ cluster }} cluster. 

  Please go ahead and close this support case.

  Thanks!
```

#### Example Usage:
*   **Post directly and close a case**:
    ```bash
    rh-support-cli comment -c 1001 -t close_case --template-var cluster="Production-OCP-1"
    ```
*   **Open the editor to review/tweak the template before posting**:
    ```bash
    rh-support-cli comment -c 1001 -t close_case --template-var cluster="Production-OCP-1" --edit
    ```

### Real-World Recipe: Proactive OpenShift Upgrade Case

Below is a complete, step-by-step recipe demonstrating how to use the CLI and templates to open a proactive support case prior to upgrading an OpenShift Container Platform (OCP) cluster, including gathering local diagnostics and attaching a `must-gather` archive.

#### 1. Define your Proactive Template
Create your template inside `~/.config/rh-support-cli/templates/proactive_upgrade.yaml`:

```yaml
_template_description: "Proactive OCP cluster upgrade planning case"
product: "Red Hat OpenShift Container Platform"
version: "{{ current_version }}"
caseType: "Other"
severity: "4 (Low)"
summary: "[Proactive] Planning upgrade on {{ cluster_name }} from {{ current_version }} to {{ next_version }}"
description: |
  We are planning an upgrade of OCP cluster "{{ cluster_name }}".
  
  Planned Upgrade Start Window: {{ planned_start | parse_date }}
  
  Cluster Details:
  - Current Version: {{ current_version }}
  - Target Version: {{ next_version }}
  - Cluster ID: {{ cluster_id }}
  
  Diagnostic Status Output:
  ```text
  {{ cluster_details }}
  ```
severity: "1 (Urgent)"
```

#### 2. Gather Cluster Status
Run diagnostic status commands against your OpenShift cluster and save the output to a temporary file:

```bash
# Authenticate against your cluster first
oc login --token=... --server=...

# Fetch status of versions, operators, machine configs, and nodes
{
  echo "=== CLUSTER VERSION ==="
  oc get clusterversion
  echo -e "\n=== CLUSTER OPERATORS ==="
  oc get clusteroperator
  echo -e "\n=== MACHINE CONFIG POOLS ==="
  oc get mcp
  echo -e "\n=== NODES STATUS ==="
  oc get nodes
} > /tmp/cluster_status.txt
```

#### 3. Gather must-gather Diagnostics
Execute an OpenShift `must-gather` diagnostic query and package it into a compressed tarball:

```bash
# Run must-gather (this will generate a local folder directory, e.g., must-gather.local.554238)
oc adm must-gather

# Package it into a compressed tarball
tar -czf /tmp/must-gather.tar.gz ./must-gather.local.*
```

#### 4. Submit Case and Upload Diagnostics
Create the case using the `proactive_upgrade` template, passing your local diagnostic statuses file contents and planned start window as template variables, and attaching the `must-gather` tarball:

```bash
rh-support-cli create \
  --template proactive_upgrade \
  --template-var cluster_name="Production-OCP-1" \
  --template-var current_version="4.12.9" \
  --template-var next_version="4.12.18" \
  --template-var planned_start="next saturday 10pm" \
  --template-var cluster_id="a1b2c3d4-e5f6-7a8b-9c0d-e1f2a3b4c5d6" \
  --template-var cluster_details="$(cat /tmp/cluster_status.txt)" \
  --attachment /tmp/must-gather.tar.gz
```

*This command automatically authenticates, renders the template with your dynamic variables (injecting the cluster status file), creates the support ticket on Red Hat Portal, and uploads the heavy `must-gather.tar.gz` diagnostic archive as an attachment to the case.*

*Note: The built-in `parse_date` Jinja2 filter utilizes Python's `dateparser` library to dynamically parse human-friendly natural date strings (like `"next saturday 10pm"`, `"tomorrow 3pm"`, or `"in 2 days"`) into a clean standard calendar timestamp format (`%d-%m-%Y %H:%M:%S`), which is extremely useful for scheduling upgrade windows automatically on Red Hat's side.*

---

## 5. Subcommands Usage

### API Backend (GraphQL & v3 Attachment Service)
The CLI communicates natively with Red Hat's CRM platform via GraphQL (`https://graphql.redhat.com`) and the v3 Attachment Service.

```bash
# Commands use the GraphQL backend natively:
rh-support-cli list
rh-support-cli show -c 12345678
rh-support-cli tui
```

The `--graphql` flag remains supported as an optional alias. Custom endpoint URLs can be configured via environment variables `RH_GRAPHQL_URL` and `RH_ATTACHMENT_API_URL`.

### Closing a Case (`close`)
The `close` subcommand operates identically to `comment`, but automatically sets the case status to `closed`:

```bash
# Interactively draft a closing comment in $EDITOR:
rh-support-cli close -c 12345678

# Close a case with a comment from a file:
rh-support-cli close -c 12345678 -f resolution.txt

# Close a case using a template:
rh-support-cli close -c 12345678 -t close_case
```

### Attachment Management (`attachment` / `attachments`)
*   **List Attachments**:
    ```bash
    rh-support-cli attachment -c 12345678 list [--include-deleted]
    ```
*   **Upload Attachments**:
    ```bash
    rh-support-cli attachment -c 12345678 upload -f /path/to/sosreport.tar.xz [-d "Sosreport after OOM"]
    # Shortcut:
    rh-support-cli attach -c 12345678 -f /path/to/sosreport.tar.xz
    ```
*   **Download Attachment**:
    ```bash
    rh-support-cli attachment -c 12345678 download -a <ATTACHMENT_UUID> [-o /path/to/destination.tar.xz]
    ```
*   **Delete Attachment**:
    ```bash
    rh-support-cli attachment -c 12345678 delete -a <ATTACHMENT_UUID> [-y]
    ```
*   **Check Attachment Status**:
    ```bash
    rh-support-cli attachment -c 12345678 status [-a <ATTACHMENT_UUID>]
    ```
### Following & Tracking Cases Locally (`follow` / `unfollow` / `following`)
Because remote watcher APIs are no longer part of Red Hat's CRM platform, the CLI maintains local case tracking inside `~/.local/state/rh-support-cli/state.yaml` (or via `$XDG_STATE_HOME`). Followed cases are monitored across local sessions, synced with remote updates, and flagged with visual indicators:

*   **Follow a single case**:
    ```bash
    rh-support-cli follow 12345678
    # or:
    rh-support-cli follow -c 12345678
    ```
*   **Follow multiple cases at once**:
    ```bash
    rh-support-cli follow 12345678 23456789 34567890
    ```
*   **Follow on behalf of another user identity**:
    ```bash
    rh-support-cli follow 12345678 --user alice
    ```
*   **List all followed cases**:
    ```bash
    rh-support-cli following
    # or with standard list filter:
    rh-support-cli list --following
    ```
*   **Filter followed cases via config bookmark**:
    ```yaml
    bookmarks:
      my-watched:
        following: true
    ```
    ```bash
    rh-support-cli list -b my-watched
    ```
*   **Unfollow single or multiple cases**:
    ```bash
    rh-support-cli unfollow 12345678
    rh-support-cli unfollow 12345678 23456789
    ```

### Synchronizing Tracked Cases & Background Service (`sync`)
Poll Red Hat's GraphQL backend in parallel across all tracked (followed and owned) cases to detect status changes and new comments:

*   **Run a manual sync**:
    ```bash
    rh-support-cli sync
    ```
*   **Sync with desktop notification**:
    If new comments or status transitions are detected, send a desktop notification using `notify-send`:
    ```bash
    rh-support-cli sync --notify
    ```
*   **Install systemd user service & timer**:
    Automatically generate and install user-level systemd service and timer units to poll periodically in the background:
    ```bash
    rh-support-cli sync --install-systemd --interval 15m

    # Enable and start the timer:
    systemctl --user daemon-reload
    systemctl --user enable --now rh-support-sync.timer
    ```

### Querying Unread Case Updates (`updates`)
Quickly inspect what changed on your tracked cases since you last checked:

*   **View unread update summary**:
    ```bash
    rh-support-cli updates
    ```
*   **View full comment content**:
    ```bash
    rh-support-cli updates --full
    ```
*   **Filter updates to a single case**:
    ```bash
    rh-support-cli updates -c 12345678
    ```
*   **Mark updates as read while reviewing**:
    ```bash
    rh-support-cli updates --mark-read
    ```
*   **Show all tracked cases (including those without updates)**:
    ```bash
    rh-support-cli updates --all
    ```

### Visual Indicators & Read Checkpoints
Both the CLI and TUI provide clear visual cues for tracked cases:
*   **Table Badges**: In `list` and the TUI case table, followed cases display `★` and cases with unread updates display `●` (with unread comment count). In `--simple-output`, they appear as `[F]` and `*`.
*   **Status Change Banner**: When viewing a case whose status changed remotely (`show` or TUI), a distinct banner highlights the change:
    ```text
    ⚡ Status Changed: Waiting on Red Hat ➔ Waiting on Customer Action Required
    ```
*   **Checkpoint Divider**: When reading a case with new comments, a dotted divider separates previously seen comments from unread ones:
    ```text
    ┈┈┈┈ 📍 Last read checkpoint (2026-10-08T10:00:00Z) │ ▼ 2 unread below ┈┈┈┈
    ```
*   **Mark Read via CLI**:
    ```bash
    rh-support-cli show -c 12345678 --mark-read
    ```

### Local State & Cache Management (`state.yaml`)
Operational state (such as followed cases, sync checkpoints, pinned tickets, and local tags) is managed in `~/.local/state/rh-support-cli/state.yaml` (or via `RH_SUPPORT_STATE` / `XDG_STATE_HOME`, with automatic fallback to legacy `XDG_CONFIG_HOME`). It uses safe atomic file operations and scopes metadata per SSO identity.

### Listing local templates
List all configured local templates, including their private descriptions and target fields. This command runs **completely offline**:
```bash
rh-support-cli list-templates
```

### Applying a Template
Apply a template over an existing case. Only fields that differ will be updated:
```bash
rh-support-cli apply -c 12345678 -t prod-standard --dry-run
```
### Custom Simple Output Fallback
If you are pipe-logging or working in a terminal that doesn't support rich formatting, pass the global `--simple-output` flag to strip all terminal escape colors and layout borders:
```bash
rh-support-cli --simple-output show -c 12345678
```

## 6. Interactive TUI Mode (`tui`)

Launch a full split-pane interactive dashboard to manage your cases directly inside your terminal:
```bash
rh-support-cli tui
```

### CLI Options:
*   `-l <layout>` / `--layout <layout>`: Select TUI split layout (`horizontal` / `mailbox` for default mailbox style or `vertical` for side-by-side).
*   `-b <bookmark_name>` / `--bookmark <bookmark_name>`: Launch the TUI using a specific bookmark of filters on startup instead of the default bookmark.
*   `--no-default-bookmark`: Launch the TUI completely unfiltered, loading all cases on startup.

### Core Features & Layout Modes
*   **Horizontal Split (Mailbox Style - Default)**: Full-width case list positioned across the top (like an email inbox), with expansive case details and comment thread below. Ideal for terminal multiplexers (`tmux`/`screen`) and widescreen monitors.
*   **Vertical Split (Side-by-Side)**: Case table on the left, full rich case details, descriptions (fully rendered Markdown), and complete scrollable comments thread on the right.
*   **Compact Zero-Margin Panes**: Panes sit flush against each other with zero margin gap, maximizing terminal columns and rows for case text and tables.
*   **Persistent Layout Preference**: Toggling layouts or configuring `default_layout: horizontal` / `tui_layout: horizontal` in `~/.config/rh-support-cli/config.yaml` or `state.yaml` automatically preserves your preferred view.
*   **Contextual Command Buttons**: Interactive action buttons (`Comment (C)`, `Template (T)`, `Bookmark (B)`, `Refresh (R)`) for easy mouse-clicks or keyboard legends.
### Keyboard Navigation & Scrolling Controls
Toggle focus between the case list and details pane using **`Tab`** or **`Shift+Tab`**. When focused on a pane:

| Command | Action |
| --- | --- |
| **`j` / `k` / `Arrow Up/Down`** | Scroll or navigate row-by-row |
| **`PageUp` / `PageDown`** | Scroll screen-by-screen |
| **`Home` / `End`** | Jump to the very top or bottom of the viewport |
| **`c`** | Open/Focus the inline Comment Pane at the bottom of the case view |
| **`a`** | Specify and upload local file attachments (Attach Modal) |
| **`t`** | Select and apply a local template (Template Modal) |
| **`b`** | Select and apply an active filter bookmark dynamically (Bookmark Modal) |
| **`v` / `l`** | Toggle split layout between vertical (side-by-side) and horizontal (mailbox style) |
| **`w`** | Toggle follow/unfollow for the currently selected case |
| **`m`** | Explicitly mark the currently selected case as read |
| **`Ctrl+Up` / `Ctrl+Down`** | Switch focus between top (case list) and bottom (details/comment) panes in horizontal split |
| **`Ctrl+Left` / `Ctrl+Right`** | Switch focus between left (case list) and right (details/comment) panes in vertical split |
| **`f`** | Zoom / Focus on the active pane (press again to exit fullscreen) |
| **`Escape`** | Exit fullscreen focus mode (unfocus layout) |
| **`Ctrl+X`** | Close / Cancel commenting from anywhere inside the TUI (Comment Pane) |
| **`r`** | Trigger contextual refresh (refreshes case details if focused; refreshes case list if table is focused) |
| **`q`** | Quit the TUI |

### TUI Unread Auto-Navigation & Dwell Mark-Read
*   **Auto-Scroll to Unread**: When selecting a case with unread comments, the details pane automatically scrolls to the yellow dotted checkpoint divider (`┈┈┈┈ 📍 Last read checkpoint ... ┈┈┈┈`).
*   **Dwell Auto-Mark Read**: Dwelling on an active case for 2.5 seconds automatically advances the read checkpoint in `state.yaml` and clears the unread badge (`●`) from the case table.
## 7. Stateful Offline Mock Support Mode (Offline & Demos)

To facilitate zero-setup demonstrations, offline testing, and development without real credentials or network connectivity, the tool includes an advanced, built-in **Stateful Offline Mock Support Mode**.

### 1. In-Process Auto-Start Mock Mode (`--mock`)
Simply pass the `--mock` flag to **any** CLI command or the interactive TUI. The tool will automatically spawn a local loopback server in a background thread, dynamically redirect all REST requests, bypass OIDC login, and serve synthetic cases.

*   **List Cases Offline**:
    ```bash
    rh-support-cli --mock list --no-default-bookmark
    ```
*   **Show Case Details Offline**:
    ```bash
    rh-support-cli --mock show -c 1001 --no-pager
    ```
*   **Create Cases & Add Comments (Stateful Mutations)**:
    Stateful mutations will automatically write back to a local temporary folder and persist for the duration of the command:
    ```bash
    rh-support-cli --mock comment -c 1001 -f "This is an offline mock comment."
    ```
*   **Launch Interactive TUI Offline**:
    ```bash
    rh-support-cli --mock tui
    ```

### 2. Standalone Dedicated Mock Portal (`mock-portal`)
If you want to run a persistent local REST API server or point external tools to your mock data:
1.  Start the standalone mock portal:
    ```bash
    rh-support-cli mock-portal --port 8080
    ```
2.  Redirect your environment variables:
    ```bash
    export RH_API_URL=http://localhost:8080
    export RH_SSO_URL=http://localhost:8080/auth/token
    export REDHAT_SUPPORT_OFFLINE_TOKEN=cli_token
    ```
3.  Any standard CLI/TUI command will now communicate against this local server, persisting state mutations in real-time.

### 3. Using a Custom Synthetic Corpus (`--mock-corpus`)
By default, `--mock` and `mock-portal` copy and read from a built-in default database inside a transient temporary directory. If you want state mutations (like case creation and comment replies) to persist across multiple separate commands permanently, specify a writable directory path:

```bash
# Execute against custom local persistent database
rh-support-cli --mock --mock-corpus ./my_custom_database list

# Mutation comments will persist inside ./my_custom_database/cases/1001.json
rh-support-cli --mock --mock-corpus ./my_custom_database comment -c 1001 -f "Persistent comment"

# Run mock portal server pointing to your custom database
rh-support-cli mock-portal --corpus ./my_custom_database --port 8080
```
