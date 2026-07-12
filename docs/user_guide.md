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

Templates are Jinja2-compatible YAML files located inside `~/.config/rh-support-cli/templates/`. They allow pre-filling case values during creation (`create`) or enforcing compliance/adding default watchers post-creation (`apply`).

### Template Metadata and Underscore (`_`) Fields
Any keys in a template file starting with an underscore `_` are treated as **private metadata** for the template itself. They are ignored when rendering, compiling, or sending payloads to the Red Hat API, keeping your case comments/descriptions safe.

#### Example `templates/default-watchers.yaml`:
```yaml
_template_description: "Add core platform team watchers to cases"
watchers:
  - alice
  - bob
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
watchers:
  - ocp-leads-mailing-list@example.com
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

### Listing local templates
List all configured local templates, including their private descriptions, target fields, and watchers. This command runs **completely offline**:
```bash
rh-support-cli list-templates
```

### Applying a Template
Apply a template over an existing case. Only fields that differ will be updated, and only missing watchers will be added:
```bash
rh-support-cli apply -c 12345678 -t default-watchers --dry-run
```

### Custom Simple Output Fallback
If you are pipe-logging or working in a terminal that doesn't support rich formatting, pass the global `--simple-output` flag to strip all terminal escape colors and layout borders:
```bash
rh-support-cli --simple-output show -c 12345678
```

---

## 6. Interactive TUI Mode (`tui`)

Launch a full split-pane interactive dashboard to manage your cases directly inside your terminal:
```bash
rh-support-cli tui
```

### CLI Options:
*   `-b <bookmark_name>` / `--bookmark <bookmark_name>`: Launch the TUI using a specific bookmark of filters on startup instead of the default bookmark.
*   `--no-default-bookmark`: Launch the TUI completely unfiltered, loading all cases on startup.

### Core Features & Layout
*   **Dual-Pane View**: Case table on the left, full rich case details, descriptions (fully rendered Markdown), and complete scrollable comments thread on the right.
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
| **`f`** | Zoom / Focus on the active pane (press again to exit fullscreen) |
| **`Escape`** | Exit fullscreen focus mode (unfocus layout) |
| **`Ctrl+X`** | Close / Cancel commenting from anywhere inside the TUI (Comment Pane) |
| **`r`** | Trigger contextual refresh (refreshes case details if focused; refreshes case list if table is focused) |
| **`q`** | Quit the TUI |

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
