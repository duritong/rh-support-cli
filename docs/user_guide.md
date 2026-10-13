# Red Hat Support Case CLI - User Guide

A modern command-line interface and interactive Terminal User Interface (TUI) to browse, create, and manage support cases against the Red Hat Support API.

---

## 1. Installation

Install the CLI and dependencies using `pip`:

```bash
# Install from source
pip install .

# Or for development (editable mode)
pip install -e .
```

*For packaging configurations and development setup, see the [Developer Guide](developer_guide.md).*

---

## 2. Authentication

The CLI requires a Red Hat Offline Token to authenticate with Red Hat SSO. Obtain your token from the [Red Hat API Portal - Offline Token Generator](https://access.redhat.com/management/api).

The CLI resolves tokens in the following order of priority:

1. **CLI Flag (`--token-file`)**:
   ```bash
   rh-support-cli --token-file /path/to/token.txt list
   ```
2. **Environment Variable (`REDHAT_SUPPORT_OFFLINE_TOKEN`)**:
   ```bash
   export REDHAT_SUPPORT_OFFLINE_TOKEN="your_offline_token_here"
   ```
3. **Configuration File**:
   ```bash
   mkdir -p ~/.config/rh-support-cli
   echo "your_offline_token_here" > ~/.config/rh-support-cli/token
   chmod 600 ~/.config/rh-support-cli/token
   ```
4. **Interactive Prompt**: If no token is detected, the CLI prompts you securely.

---

## 3. Configuration & Bookmarks

Configuration is stored in `~/.config/rh-support-cli/config.yaml` (or via `RH_SUPPORT_CONFIG`).

### Example `config.yaml`

```yaml
# Default bookmark applied automatically to list and search commands
default_bookmark: ocp-team

# Default template used when creating new cases interactively
default_create_template: base-template

# Default language for Knowledgebase (KCS) queries (default: en)
default_kcs_language: en

# Background debug logging (redacted HTTP requests/responses)
debug_file: ~/.config/rh-support-cli/debug_log.txt

# Local Disk Cache & Polling Settings
cache:
  enabled: true                 # Set to false to disable all disk caching globally
  list_ttl: 60                  # Seconds before a case list query re-fetches from API (default: 60s)
  catalog_ttl: 86400            # Seconds before product catalog re-fetches (default: 24h)
  tui_poll_interval: 60         # Seconds between background polls in TUI (default: 60s, 0 to disable)

# Filter bookmarks
bookmarks:
  ocp-team:
    product: "OpenShift Container Platform"
    status: ["Waiting on Red Hat", "Waiting on Customer"]
    severity: ["High", "Urgent"]
    language: "en"              # Preferred KCS search language for this bookmark

  my-rhel:
    product: "Red Hat Enterprise Linux"
    status: ["open"]
```

### Global Cache Control (`--no-cache`)
To bypass reading from or writing to the local disk cache, pass `--no-cache` or set `export RH_NO_CACHE=1`:
```bash
rh-support-cli list --no-cache
rh-support-cli show -c 12345678 --no-cache
```

---

## 4. Case Sizing Templates

Templates are Jinja2-compatible YAML files stored in `~/.config/rh-support-cli/templates/`. They allow pre-filling case values during creation (`create`) or updating existing cases into compliance (`apply`).

### Template Metadata and Underscore (`_`) Fields
Keys starting with an underscore (`_`) are treated as private metadata for the template itself. They are ignored when submitting payloads to the API:

```yaml
_template_description: "Standard production incident template"
_target_sla: "2h"
product: "OpenShift Container Platform"
version: "4.12"
severity: "2 (High)"
caseType: "Standard"
```

### Comment Templates
Templates can also pre-fill comment bodies and transition status in `comment` and `close`:

```yaml
# templates/close_case.yaml
_template_description: "Standard verification and close comment"
status: "closed"
comment: |
  Hi Support,

  We have verified the fix on our {{ cluster }} cluster.
  Please proceed with closing this ticket.

  Thanks!
```

```bash
# Apply template with variables:
rh-support-cli comment -c 1001 -t close_case --template-var cluster="Production-OCP-1"
```

---

## 5. Subcommands Reference

### 5.1 Listing Cases (`list`)
Lists support cases matching account, status, severity, product, or owner filters:

```bash
# List all cases (uses default bookmark if configured):
rh-support-cli list

# List high severity cases for an account:
rh-support-cli list --account 12345 --status "Waiting on Red Hat" --severity High

# Filter using an explicit bookmark:
rh-support-cli list -b my-rhel

# Fetch all cases across all pages:
rh-support-cli list --status open --all

# Limit number of cases returned:
rh-support-cli list --limit 50
```

### 5.2 Showing Case Details (`show`)
Displays complete case metadata, descriptions, attachments, and the comment thread (piped to `$PAGER` by default):

```bash
# View case details:
rh-support-cli show -c 12345678

# Print directly to stdout without pager:
rh-support-cli show -c 12345678 --no-pager

# Mark unread updates on this case as read:
rh-support-cli show -c 12345678 --mark-read
```

### 5.3 Case Browser Link (`link`)
Generates the browser URL for a support case (bypasses authentication):

```bash
rh-support-cli link -c 12345678
# Output: https://access.redhat.com/support/cases/12345678
```

### 5.4 Creating Cases (`create`)
Creates a new support ticket interactively or via command-line flags:

```bash
# Interactive mode (prompts for product, version, summary, severity):
rh-support-cli create

# Non-interactive mode:
rh-support-cli create \
  --product "Red Hat Enterprise Linux" \
  --version "9.0" \
  --summary "Kernel panic on boot" \
  --description-file ./panic.log \
  --severity "High" \
  --type "Bug" \
  --attachment /tmp/sosreport.tar.xz

# Create from template:
rh-support-cli create --template proactive_upgrade --template-var cluster="OCP-Prod"
```

### 5.5 Commenting & Status Updates (`comment`)
Posts a comment to an existing case and optionally transitions its status (defaults to `Waiting on Red Hat`):

```bash
# Add comment from file and transition status to Waiting on Red Hat:
rh-support-cli comment -c 12345678 -f update.txt -s redhat

# Post an informational comment WITHOUT changing the case status:
rh-support-cli comment -c 12345678 -f update.txt --no-status-change
# or using the alias / status value:
rh-support-cli comment -c 12345678 -f update.txt --keep-status
rh-support-cli comment -c 12345678 -f update.txt -s none

# Open $EDITOR with last comment for context:
rh-support-cli comment -c 12345678 --include-previous-comments 2

# Edit draft file in editor before submitting:
rh-support-cli comment -c 12345678 -f draft.txt --edit
```
### 5.6 Closing Cases (`close`)
Closes a support case with a final comment (automatically sets status to `closed`):

```bash
# Interactively draft closing comment in $EDITOR:
rh-support-cli close -c 12345678

# Close case with comment from file:
rh-support-cli close -c 12345678 -f resolution.txt
```

### 5.7 Comment & Description Formatting (`--format`)
The Red Hat Customer Portal displays comments and descriptions as Rich Text (HTML). Unformatted plain text with raw newlines collapses into single lines on the web portal.

The CLI formats comments using `--format`:
* **`markdown` (Default)**: Converts GitHub-Flavored Markdown to HTML with soft line breaks:
  * Single newlines (`\n`) become `<br />` breaks.
  * Double newlines (`\n\n`) become `<p>...</p>` paragraph blocks.
  * Code blocks (`` ``` ``) become monospace `<pre><code>...</code></pre>` blocks.
  * Inline code (`` `code` ``) becomes `<code>...</code>`.
* **`plain`**: Escapes HTML entities and wraps text in `<p>` and `<br />` tags without parsing Markdown syntax. Recommended for raw diagnostic dumps or log files containing characters like `*`, `#`, or `_`.
* **`html`**: Sends verbatim HTML directly to the portal.
* **`raw`**: Bypasses formatting and sends unformatted plain text.

```bash
# Post Markdown formatted comment (default):
rh-support-cli comment -c 12345678 -f update.md

# Post raw log output preserving line breaks without Markdown parsing:
rh-support-cli comment -c 12345678 -f /var/log/messages --format plain
```

### 5.8 Attachment Management (`attachment` / `attach` / `download`)
Manage diagnostic archives and log bundles attached to support cases:

```bash
# Upload an attachment (shortcut):
rh-support-cli attach -c 12345678 -f /tmp/sosreport.tar.xz

# Upload with optional description:
rh-support-cli attachment -c 12345678 upload -f /tmp/sosreport.tar.xz -d "Post-OOM report"

# List attachments:
rh-support-cli list-attachments 12345678

# Download an attachment by filename or UUID:
rh-support-cli download -c 12345678 -a sosreport.tar.xz -o /tmp/sosreport.tar.xz

# Delete an attachment:
rh-support-cli attachment -c 12345678 delete -a <ATTACHMENT_UUID> -y
```

#### Automated Multipart Uploads (> 5 GB up to 5 TB)
Files exceeding 5 GB are **automatically transferred via multipart uploads**.
* **No Manual Splitting**: You do not need to run `split -b`. The CLI automatically splits, streams, and completes the upload on Red Hat's storage backend.
* **Low-Memory Streaming**: Large files stream in small 8 MB memory blocks, maintaining client RAM usage under 50 MB regardless of file size.
* **Live Progress Bar**: Displays real-time transfer speed (MB/s), remaining time, and upload percentage.
* **Automatic Abort on Failure**: If an upload is cancelled (`Ctrl+C`) or interrupted, the CLI aborts the session cleanly to release storage resources.

### 5.9 Following & Tracking Cases Locally (`follow` / `unfollow` / `following`)
Track cases locally in `~/.local/state/rh-support-cli/state.yaml` to monitor tickets across CLI and TUI sessions:

```bash
# Follow one or more cases:
rh-support-cli follow 12345678
rh-support-cli follow 12345678 23456789

# List followed cases:
rh-support-cli following

# Unfollow cases:
rh-support-cli unfollow 12345678
```

### 5.10 Synchronizing Tracked Cases & Systemd Timer (`sync` / `install-systemd` / `systemd`)
Polls Red Hat's backend across all followed and owned cases in parallel to detect remote updates:

```bash
# Run manual synchronization:
rh-support-cli sync

# Sync with desktop notifications on new comments or status transitions:
rh-support-cli sync --notify
```

#### Installing the Background Systemd Timer
You can automate regular case synchronization using a systemd user timer:

```bash
# Install and immediately start the timer (default: 15m):
rh-support-cli install-systemd --enable

# Install with custom interval and desktop notifications:
rh-support-cli install-systemd --interval 30m --notify --enable

# Inspect timer and service status:
rh-support-cli systemd status

# Stop and uninstall the timer:
rh-support-cli systemd uninstall
```
### 5.11 Querying Unread Updates (`updates`)
Inspects what changed across your tracked cases since your last checkpoint:

```bash
# View summary of unread updates:
rh-support-cli updates

# View full unread comment bodies:
rh-support-cli updates --full

# Mark all inspected updates as read:
rh-support-cli updates --mark-read
```

#### Tracking Indicators
* **Table Badges**: Followed cases display `★` (`[F]` in `--simple-output`); cases with unread updates display `●` with comment count (`*` in `--simple-output`).
* **Status Banner**: When viewing a case whose status changed remotely, a status transition banner is displayed.
* **Checkpoint Divider**: In comment threads, a divider (`┈┈┈┈ 📍 Last read checkpoint ... ┈┈┈┈`) marks previously seen comments.

### 5.12 Searching Cases & Knowledgebase (`search`)
Unified search across your support cases (primary focus) and the Red Hat Knowledgebase (KCS):

```bash
# Search support cases by keyword (default):
rh-support-cli search "ingress cert expired"

# Search by case number:
rh-support-cli search 04526149

# Search Red Hat Knowledgebase (KCS) solutions and articles:
rh-support-cli search "failed to mount API filesystem" --type kcs

# Search both cases and knowledgebase solutions simultaneously:
rh-support-cli search "kernel panic" --type all

# Search knowledgebase and open top result in default web browser:
rh-support-cli search "CRI-O pod sandbox deadline" --type kcs --open

# Fast offline search across locally cached cases, descriptions, and comments:
rh-support-cli search "out of memory" --local

# Filter KCS results by language code (default: 'en'):
rh-support-cli search "networking" --type kcs --language ja

# Search cases incorporating default bookmark (or bypass with --no-default-bookmark):
rh-support-cli search "storage" --no-default-bookmark
```

All search results include full browser URLs (`https://access.redhat.com/support/cases/<num>` and `https://access.redhat.com/solutions/<id>`).

### 5.13 Template Commands (`apply` & `list-templates`)
```bash
# List local templates offline:
rh-support-cli list-templates

# Apply a template over an existing case (dry run):
rh-support-cli apply -c 12345678 -t prod-standard --dry-run

# Apply and commit template updates:
rh-support-cli apply -c 12345678 -t prod-standard
```

### 5.14 Machine-Readable Output (`--json` & `--format`)
For scripting and piping with `jq`, any query command supports structured output:
* `--json` / `-j`: Outputs pure JSON without headers or banners.
* `--format <text|json|yaml>`: Choose output serialization.

```bash
# Extract case numbers of open cases:
rh-support-cli list --status open --json | jq -r '.[].case_number'

# Export case details as YAML:
rh-support-cli show -c 12345678 --format yaml
```

### 5.15 Simple Output Fallback (`--simple-output`)
Strips all ANSI coloring, box-drawing characters, and layout grids for clean pipe processing:
```bash
rh-support-cli --simple-output list
```

---

## 6. Interactive TUI Mode (`tui`)

Launch an interactive split-pane terminal dashboard:

```bash
rh-support-cli tui
```

### Options:
* `-l <layout>` / `--layout <layout>`: Split layout (`horizontal` / `mailbox` default or `vertical` side-by-side).
* `-b <name>` / `--bookmark <name>`: Launch with a specific bookmark filter.
* `--no-default-bookmark`: Launch unfiltered.
* `--no-cache`: Bypass disk cache on startup.

### Keyboard Controls:
| Key | Action |
| :--- | :--- |
| **`Tab`** / **`Shift+Tab`** | Toggle focus between case list and details pane |
| **`j` / `k` / `Up/Down`** | Navigate row-by-row / scroll lines |
| **`PageUp` / `PageDown`** | Scroll screen-by-screen |
| **`c`** | Open inline Comment drafting pane |
| **`a`** | Upload file attachment (Attach Modal) |
| **`d`** | Download case attachment (Download Modal) |
| **`t`** | Select and apply template (Template Modal) |
| **`b`** | Switch active filter bookmark (Bookmark Modal) |
| **`v` / `l`** | Toggle layout (horizontal mailbox vs vertical side-by-side) |
| **`w`** | Toggle follow/unfollow for active case |
| **`m`** | Mark active case as read |
| **`f`** | Zoom / Fullscreen toggle for active pane |
| **`r`** | Contextual refresh |
| **`q`** | Quit TUI |

*For internal TUI architecture, Textual view-swapping, and threading details, see the [Developer Guide](developer_guide.md).*

---

## 7. Stateful Offline Mock Mode (`--mock` & `mock-portal`)

For demos, offline testing, and development without network access:

### In-Process Mock (`--mock`)
Pass `--mock` to any CLI command or the TUI:
```bash
rh-support-cli --mock list
rh-support-cli --mock show -c 1001 --no-pager
rh-support-cli --mock tui
```

### Standalone Mock Portal (`mock-portal`)
Run a persistent local mock server:
```bash
rh-support-cli mock-portal --port 8080
export RH_API_URL=http://localhost:8080
export RH_SSO_URL=http://localhost:8080/auth/token
export REDHAT_SUPPORT_OFFLINE_TOKEN=cli_token
```

*For mock server implementation details and test corpus generation, see the [Developer Guide](developer_guide.md).*
