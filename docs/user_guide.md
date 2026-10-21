# Red Hat Support Case CLI - User Guide

A command-line interface and interactive Terminal User Interface (TUI) to browse, create, and manage Red Hat support cases.

---

## 1. Installation

Install the package and dependencies using `pip`:

```bash
# Standard installation
pip install .

# Editable development installation
pip install -e .
```

*For build specifications, virtual environments, and dependency floors, see the [Developer Guide](developer_guide.md).*

---

## 2. Authentication

The CLI authenticates with Red Hat SSO using an Offline Token. Generate a token from the [Red Hat API Portal - Offline Token Generator](https://access.redhat.com/management/api).

The CLI resolves tokens in the following order of precedence:

1. **CLI Flag (`--token-file`)**:
   ```bash
   rh-support-cli --token-file /path/to/token.txt list
   ```
2. **Environment Variable (`REDHAT_SUPPORT_OFFLINE_TOKEN`)**:
   ```bash
   export REDHAT_SUPPORT_OFFLINE_TOKEN="your_offline_token_here"
   ```
3. **Default Configuration File**:
   ```bash
   mkdir -p ~/.config/rh-support-cli
   echo "your_offline_token_here" > ~/.config/rh-support-cli/token
   chmod 600 ~/.config/rh-support-cli/token
   ```
4. **Interactive Prompt**: Prompts securely on the terminal if no credential source is found.

*Note: If attachment access reports a missing `api.graphql` scope, generate a **new offline token** at <https://access.redhat.com/management/api>. Older offline-token grants may lack this scope; refreshing existing access tokens or clearing cache cannot upgrade an older token grant.*

---

## 3. Configuration & Bookmarks

The default configuration file is located at `~/.config/rh-support-cli/config.yaml`.

### Example `config.yaml`

```yaml
# Default bookmark applied automatically to list and search commands
default_bookmark: ocp-team

# Default template used when creating new cases interactively
default_create_template: base-template

# Default language for Knowledgebase (KCS) queries (default: en)
default_kcs_language: en

# Default comment display order ('newest' or 'oldest', default: newest)
comments_order: newest

# Background HTTP debug logging (redacted requests/responses)
debug_file: ~/.config/rh-support-cli/debug_log.txt

# Local Disk Cache & Polling Settings
cache:
  enabled: true                 # Set to false to disable all disk caching
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

### Custom Configuration (`--config-file` / `RH_SUPPORT_CONFIG`)

Specify an alternate configuration file using `--config-file` or `RH_SUPPORT_CONFIG`:

```bash
rh-support-cli --config-file /path/to/custom_config.yaml list
```

- Tilde (`~`) paths are expanded automatically.
- Explicit bookmarks passed on the CLI overlay default bookmark settings unless `--no-default-bookmark` is specified.
- If a custom configuration file is missing, unreadable, or invalid YAML, the command exits with an error before initiating API requests.

### Filter Bookmarks

Bookmarks define reusable sets of query filters (`product`, `status`, `severity`, `account`, `owner`, `language`).
- Apply bookmarks with `-b <name>` or `--bookmark <name>`.
- Multiple `-b` flags merge filters in order.
- Use `--no-default-bookmark` to ignore configured defaults and query unfiltered data.

### Cache Control (`--no-cache`)

The CLI caches ticket metadata, product catalogs, and search results under `$XDG_CACHE_HOME/rh-support-cli/` to deliver instant responses.

To bypass reading from or writing to the local disk cache:

```bash
# Bypass cache for a single command:
rh-support-cli list --no-cache
rh-support-cli show -c 12345678 --no-cache

# Bypass cache globally via environment variable:
export RH_NO_CACHE=1
```

### Debugging & HTTP Logging (`--debug` / `--debug-file`)

Inspect API traffic with automated credential redaction:

* `--debug`: Prints redacted HTTP requests and responses to `stderr`.
* `--debug-file <path>`: Logs full, untruncated JSON requests and non-streamed responses to the specified file without console noise.
* **Sensitive Content Warning**: Automated redaction masks tokens, authorization headers, cookies, and presigned S3 URLs, but logs still contain case descriptions and diagnostic error payloads. Treat debug log files as sensitive data.
* **Omitted Payloads**: Opaque binary request bodies and streamed file download response bodies are deliberately omitted from debug logs rather than buffered in memory.

```bash
# Stream debug logs to console:
rh-support-cli --debug list

# Log full payloads to file:
rh-support-cli --debug-file /tmp/rh_debug.log show -c 12345678
```

### Environment Variables

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `REDHAT_SUPPORT_OFFLINE_TOKEN` | Red Hat SSO offline API token | None (prompted/resolved) |
| `RH_SUPPORT_CONFIG` | Path to custom YAML configuration file | `~/.config/rh-support-cli/config.yaml` |
| `RH_SUPPORT_STATE` | Path to persistent local operational state | `~/.local/state/rh-support-cli/state.yaml` |
| `RH_NO_CACHE` | Bypass local disk cache globally (`1` or `true`) | `0` |
| `RH_GRAPHQL_URL` | Override CRM GraphQL API endpoint | `https://graphql.redhat.com` |
| `RH_ATTACHMENT_API_URL` | Override Attachment API endpoint | `https://api.access.redhat.com/support` |
| `RH_KCS_SEARCH_URL` | Override Hydra KCS search endpoint | `https://access.redhat.com/hydra/rest/search/kcs` |
| `RH_SSO_URL` | Override Red Hat SSO OAuth token endpoint | `https://sso.redhat.com/...` |
| `EDITOR` | Text editor for comments and descriptions | `vi` |
| `FORCE_COLOR` | Force ANSI colors when piped or redirected | `auto` |

---

## 4. Case Templates

Templates are Jinja2-compatible YAML files located in `~/.config/rh-support-cli/templates/` (or configured via `templates_dir`). They pre-fill values during creation (`create`) or bring existing cases into compliance (`apply`).

### Template Variables and Metadata

- Keys starting with an underscore (`_`) are private template metadata and are ignored by API mutations.
- Dynamic variables are passed via `--template-var key=value`. All required variables without defaults must be supplied.
- Use Jinja2 default filters for optional values: `{{ cluster | default('Production') }}`.

```yaml
# templates/incident.yaml
_template_description: "Standard production cluster incident template"
product: "OpenShift Container Platform"
version: "4.12"
severity: "2 (High)"
caseType: "Standard"
summary: "[{{ cluster }}] {{ issue_title }}"
description: |
  Environment: {{ cluster }}
  Impact: Production degraded
```

### Comment Templates

Templates can also pre-fill comment bodies and transition status in `comment` and `close`:

```yaml
# templates/close_case.yaml
_template_description: "Verification and closure comment"
status: "closed"
comment: |
  Hi Support,

  We have verified the fix on cluster {{ cluster }}. Please proceed with closing this case.
```

```bash
rh-support-cli comment -c 12345678 -t close_case --template-var cluster="OCP-Prod-1"
```

---

## 5. Subcommands Reference

### 5.1 Listing Cases (`list`)

List support cases matching account, status, severity, product, or owner filters:

```bash
# List cases (filtered by default bookmark if configured; unfiltered if none):
rh-support-cli list

# Explicitly filter by open status:
rh-support-cli list --status open

# Filter cases followed locally:
rh-support-cli list --following

# Filter by account, status, and severity:
rh-support-cli list --account 12345 --status "Waiting on Red Hat" --severity High

# Filter using an explicit bookmark:
rh-support-cli list -b my-rhel

# Fetch all cases across all pages:
rh-support-cli list --status open --all

# Limit number of cases returned:
rh-support-cli list --limit 50
```

### 5.2 Showing Case Details (`show`)

Display ticket summary, status, metadata, description, attachments, and comment threads:

```bash
# View case details (paged via less):
rh-support-cli show -c 12345678

# View case details without pager:
rh-support-cli show -c 12345678 --no-pager

# Mark comments as read locally:
rh-support-cli show -c 12345678 --mark-read

# Reverse comment order (oldest first):
rh-support-cli show -c 12345678 --order oldest

# Export incident shift-handoff summary (Markdown, Slack, or JSON):
rh-support-cli show -c 12345678 --export
rh-support-cli show -c 12345678 --export slack
rh-support-cli show -c 12345678 --export json
```

### 5.3 Case Browser Link (`link`)

Print the Red Hat Customer Portal URL for a ticket:

```bash
rh-support-cli link -c 12345678
# Output: https://access.redhat.com/support/cases/12345678
```

### 5.4 Creating Cases (`create`)

Create support tickets interactively or non-interactively using command-line flags and templates:

* **Interactive Mode**: Prompts for required fields (product, version, summary, severity) and launches `$EDITOR` to compose the description.
* **Non-Interactive Mode (`--yes`)**: Requires all fields to be supplied via flags or templates. Bypasses confirmation prompts.
* **Attachments**: Files passed via `--attachment` are uploaded automatically after the case is created.
* **Draft Preservation on Failure**: If validation or API submission fails, the generated description draft is preserved locally and its path is printed to stderr. Answering `n` at the interactive confirmation prompt discards the generated draft intentionally.
* **Partial Success & Safe Retries**: If case creation succeeds on the portal but an attachment upload fails or is cancelled, the command exits with status 1 and outputs a receipt with `created: true, success: false`. Do not rerun `create` (which would create a duplicate ticket); check the newly created case in the portal and attach missing files using `rh-support-cli attach`.
* **Unknown Submission Outcomes**: If a network timeout or connection drop occurs during creation, verify whether the case exists in the portal before attempting creation again.
* **Identity Resolution**: Case creation resolves your SSO username from your token (or `REDHAT_SUPPORT_USERNAME`). An explicit account must match the contact's resolved account.

```bash
# Interactive case creation:
rh-support-cli create

# Non-interactive creation with attachments and JSON receipt:
rh-support-cli create --yes --json \
  --product "Red Hat Enterprise Linux" \
  --version "9.0" \
  --summary "Kernel panic on boot" \
  --description-file ./panic.log \
  --severity "High" \
  --type "Bug" \
  --attachment /tmp/sosreport.tar.xz

# Create from template with required variables:
rh-support-cli create --template incident --template-var cluster="OCP-Prod" --template-var issue_title="API unresponsive"

# Ignore default create template:
rh-support-cli create --no-default-template
```

### 5.5 Commenting & Status Updates (`comment`)

Post comments and optionally transition case status:

* **Default Status Transition**: If the case is currently waiting on customer action (`Waiting on Customer` or `Waiting on Customer Action Required`), posting a comment transitions it to `Waiting on Red Hat` (`In Progress`). If the case is already in progress, waiting on engineering, or closed, the status remains unchanged.
* **Post-Editor Recheck**: For interactive editor comments, the case status is checked again immediately before submission so transitions reflect live state.
* **Explicit Status Override**: Set status explicitly using `-s <status>` or `--status <status>` (e.g. `-s closed`, `-s customer`, `-s redhat`) or via a template's `status:` field.
* **Preserve Status**: Pass `--no-status-change`, `--keep-status`, or `-s none`/`keep` to guarantee no status transition occurs.
* **Fail-Closed on Unreachable Status**: If live status cannot be read during a default transition, submission halts without posting and your draft is preserved.
* **Partial Success & Safe Retries**: If a comment is posted successfully but a requested status update fails, the command exits with status 1 and reports `comment_posted: true, status_confirmed: false`. Do not repost the comment (which creates duplicates); inspect the case in the portal and update only its status.
* **Unknown Submission Outcomes**: If an unknown outcome occurs (e.g. timeout reading server response), check the portal before retrying. Do not blindly repost.
* **Recovery Drafts vs Retained Copies**: An unsent draft preserved after a failed submission can be safely resumed. If a comment was confirmed but local cleanup of the temporary file failed, a copy remains on disk; do not resubmit this retained copy.

```bash
# Comment from file and transition to Waiting on Red Hat:
rh-support-cli comment -c 12345678 -f update.txt -s redhat

# Post an informational update without changing case status:
rh-support-cli comment -c 12345678 -f update.txt --no-status-change
rh-support-cli comment -c 12345678 -f update.txt --keep-status
rh-support-cli comment -c 12345678 -f update.txt -s none

# Open $EDITOR with last 2 comments for context:
rh-support-cli comment -c 12345678 --include-previous-comments 2

# Load draft file into editor for adjustment before posting:
rh-support-cli comment -c 12345678 -f draft.txt --edit
```

### 5.6 Closing Cases (`close`)

Close a support ticket with a final comment (automatically sets status to `Closed`):

```bash
# Interactively draft closing comment in $EDITOR:
rh-support-cli close -c 12345678 --include-previous-comments 1

# Close case non-interactively from file:
rh-support-cli close -c 12345678 -f resolution.txt
```

### 5.7 Rich Text Formatting (`--format`)

Format comment bodies for the Red Hat Customer Portal:

* `markdown` (default): Converts standard Markdown (headings, lists, bold, backtick code blocks) to clean HTML for portal rendering.
* `plain`: Escapes special characters and converts line breaks to `<br />`.
* `html`: Passes raw HTML directly to the portal.
* `raw`: Submits plain text without transformation.

```bash
rh-support-cli comment -c 12345678 -f notes.md --format markdown
rh-support-cli comment -c 12345678 -f logs.txt --format plain
```

### 5.8 Attachment Management (`attachment` / `attach` / `download`)

Upload, download, inspect, and delete case attachments:

```bash
# List attachments for a case:
rh-support-cli attachment -c 12345678 list
rh-support-cli list-attachments 12345678

# Upload a single file (shortcut):
rh-support-cli attach -c 12345678 -f /tmp/sosreport.tar.xz

# Download an attachment by UUID or filename:
rh-support-cli download -c 12345678 -a <attachment-uuid> -o /tmp/sosreport.tar.xz

# Inspect attachment upload processing status:
rh-support-cli attachment -c 12345678 status

# Delete an attachment:
rh-support-cli attachment -c 12345678 delete -a <attachment-uuid>
```

#### Upload & Download Safety Guarantees
* **Source Stability**: Keep upload source files unchanged during transfer; modifying source files mid-flight causes transfer verification errors.
* **Download Publication**: Downloads stream to a private temporary file and publish via hard link, refusing existing destination files (including symlinks or files created during transfer) to prevent overwriting local files. Specify a new output path (`-o`) if the file already exists.
* **Multipart Streaming (> 5 GB up to 5 TB)**: Files larger than 5 GiB stream in 5 GiB chunks using bounded 8 MiB transfer blocks to prevent local memory exhaustion. Interrupted uploads (`Ctrl+C`) trigger an automated session abort on Red Hat's storage backend.
* **Missing `api.graphql` Scope**: If attachment operations report a missing scope error, generate a new offline token at <https://access.redhat.com/management/api>. Older offline tokens lack this scope, and access-token refreshes cannot escalate an older grant.

### 5.9 Following & Tracking Cases Locally (`follow` / `follow-account` / `unfollow` / `unfollow-account` / `following`)

Track support tickets locally in `~/.local/state/rh-support-cli/state.yaml` to monitor updates and unread comments:

```bash
# Follow specific cases:
rh-support-cli follow 12345678
rh-support-cli follow 12345678 23456789

# Preview follow action without changing state:
rh-support-cli follow 12345678 --dry-run
rh-support-cli unfollow 12345678 --dry-run

# Follow all open cases for an account (default status: open):
rh-support-cli follow-account 123456
rh-support-cli follow --account 123456

# Follow all cases for an account (both open and closed):
rh-support-cli follow-account 123456 --all
rh-support-cli follow-account 123456 --status all

# Follow cases matching a specific status:
rh-support-cli follow-account 123456 --status customer
rh-support-cli follow-account 123456 --status "Waiting on Red Hat"

# Preview which account cases would be followed:
rh-support-cli follow-account 123456 --dry-run

# List locally followed cases:
rh-support-cli following

# Filter followed cases by bookmark or user:
rh-support-cli following -b ocp-team
rh-support-cli following -u colleague_sso

# Sync latest status from account before listing:
rh-support-cli following --sync

# Unfollow specific cases:
rh-support-cli unfollow 12345678

# Unfollow all followed cases for an account:
rh-support-cli unfollow-account 123456
rh-support-cli unfollow --account 123456

# Unfollow only closed cases of an account:
rh-support-cli unfollow-account 123456 --status closed

# Preview which cases would be unfollowed:
rh-support-cli unfollow-account 123456 --dry-run
```

#### Account Tracking Scope & Bookmarks
* **Supported Bookmark Fields**: Account commands (`follow-account`, `unfollow-account`) accept bookmark filters for `account` (or `accountNumber`), `status`, `severity`, and `user`.
* **Unsupported Filter Rejection**: Other bookmark directives (such as `product` or `owner`) are rejected before execution with an error. If your default bookmark contains unsupported fields, pass `--no-default-bookmark`.
* **CLI Precedence**: Explicit CLI options (`--account`, `--status`, `--severity`, `--user`) override corresponding bookmark values. Passing `--all` overrides bookmark status to include all cases.
* **Account Follow Previews**: `follow-account --dry-run` queries matching remote cases and previews the operation while leaving all local tracking records and read checkpoints untouched, including cases you own.
* **Local Unfollow Selection**: `unfollow-account` selects cases using local tracking data in `state.yaml`. If a tracked case is missing an account number in state, locally cached details are consulted; cases whose account cannot be identified locally remain followed. Filtered unfollow evaluates this local tracking data.

### 5.10 Synchronizing Tracked Cases & Background Timer (`sync` / `install-systemd` / `systemd`)

Poll Red Hat Support APIs across tracked cases in parallel to detect remote updates:

```bash
# Run manual synchronization across all tracked cases:
rh-support-cli sync

# Sync cases for a specific account or user:
rh-support-cli sync --account 12345 -u myself@redhat.com

# Sync with desktop notifications on new comments or status transitions:
rh-support-cli sync --notify
```

#### Automating with Systemd User Timer

```bash
# Install and enable the systemd timer (default interval: 15m):
rh-support-cli install-systemd --enable

# Install with custom interval and desktop notifications:
rh-support-cli install-systemd --interval 30m --notify --enable

# Check timer status:
rh-support-cli systemd status

# Uninstall the timer:
rh-support-cli systemd uninstall
```

### 5.11 Querying Unread Updates (`updates`)

Inspect new comments and status transitions across tracked tickets since your last checkpoint:

```bash
# View summary of unread updates:
rh-support-cli updates

# Filter updates to a single case:
rh-support-cli updates -c 12345678

# Include tracked cases with no unread updates:
rh-support-cli updates --all

# View full unread comment bodies:
rh-support-cli updates --full

# Mark all inspected updates as read:
rh-support-cli updates --mark-read
```

#### Visual Indicators
* **Table Badges**: Followed cases display `★` (`[F]` in `--simple-output`); cases with unread updates display `●` with comment count (`*` in `--simple-output`).
* **Status Banner**: Displays a status transition banner when a case status changes remotely.
* **Checkpoint Divider**: Divides unread comments from previously read comments (`┈┈┈┈ 📍 Last read checkpoint ... ┈┈┈┈`).

### 5.12 Searching Cases & Knowledgebase (`search`)

Unified search across support cases and the Red Hat Knowledgebase (KCS):

```bash
# Search support cases by keyword (default):
rh-support-cli search "ingress cert expired"

# Search by case number:
rh-support-cli search 04526149

# Search Red Hat Knowledgebase (KCS) solutions and articles:
rh-support-cli search "failed to mount API filesystem" --type kcs

# Search cases and KCS solutions simultaneously:
rh-support-cli search "kernel panic" --type all

# Search KCS and open top result in your default browser:
rh-support-cli search "CRI-O pod sandbox deadline" --type kcs --open

# Fast offline search across locally cached cases and comments:
rh-support-cli search "out of memory" --local

# Filter KCS results by language code (default: 'en'):
rh-support-cli search "networking" --type kcs --language ja
```

* **Offline Local Search (`--local`)**: Searches locally cached case summaries and descriptions without network access. Requires an offline credential source to resolve the private cache. KCS queries require live remote access and are not available in local mode.

### 5.13 Template Compliance (`apply` & `list-templates`)

```bash
# List local templates:
rh-support-cli list-templates

# Preview template updates against an existing case (dry run):
rh-support-cli apply -c 12345678 -t prod-standard --dry-run

# Apply and commit template updates:
rh-support-cli apply -c 12345678 -t prod-standard
```

* **Supported Fields Contract**: `apply` updates only mutable fields: `status`, `severity`, `summary`, and `description`. Templates containing other fields (such as `product`, `version`, or `caseType` from creation templates) are rejected with an error before any API mutation, even during `--dry-run`. Only fields prefixed with an underscore (`_`) are treated as metadata and ignored.

Example apply-compatible template:
```yaml
# templates/prod-standard.yaml
_description: "Standard production compliance template"
severity: "2 (High)"
status: "In Progress"
```

### 5.14 Structured Machine-Readable Output (`--json` & `--format`)

For scripting and pipeline integration with `jq`:

* `--json` / `-j`: Emits pure JSON without terminal formatting or banners.
* `--format <text|json|yaml>`: Selects serialization format for `stdout`.

#### Content Format vs Output Serialization
Command-level `--format <markdown|plain|html|raw>` on `create`, `comment`, and `close` controls comment/description content formatting, whereas global `--format <json|yaml>` controls `stdout` serialization:

```bash
# Output a YAML receipt while formatting the comment body as Markdown:
rh-support-cli --format yaml comment -c 12345678 -f update.md --format markdown
```

#### Automation & Write Receipts
* Machine-readable data (JSON/YAML) is written exclusively to `stdout`. Prompts, progress indicators, and error messages go to `stderr`.
* Write commands (`create`, `comment`, `close`) emit a structured receipt dictionary containing fields such as `case_number`, `success`, `created`, `comment_posted`, and `status_confirmed`.
* Incomplete outcomes (e.g. case created but attachment failed, or comment posted but status update failed) emit a receipt with `success: false` and exit with status 1.

```bash
# Extract case numbers from open cases:
rh-support-cli list --status open --json | jq -r '.[].case_number'

# Export case details as YAML:
rh-support-cli show -c 12345678 --format yaml
```

### 5.15 Plain Text Output (`--simple-output`)

Strips ANSI color sequences, borders, and box-drawing characters for clean piping:

```bash
rh-support-cli --simple-output list
rh-support-cli --simple-output show -c 12345678
```

### 5.16 Shell Auto-Completion (`completion`)

Generate shell completion for commands, arguments, bookmarks, products, and case numbers:

```bash
# Enable completion in current bash session:
eval "$(rh-support-cli completion)"

# Persist completion in ~/.bashrc:
echo 'eval "$(rh-support-cli completion)"' >> ~/.bashrc
```

*Context-aware completion suggests products from the catalog, status values, bookmark names, template names, and case numbers.*

### 5.17 Health Diagnostics (`doctor`)

Diagnose local configuration, credentials, directory permissions, and system timers:

* **Online Probe (`--online`)**: Sends an unauthenticated GraphQL `HEAD` probe with a 3-second timeout to verify network reachability. An HTTP response (including 401/403) confirms reachability, but does not validate credentials or API permissions.
* **Exit Status**: Only failed checks cause `doctor` to exit with status 1; warnings (such as permissive token file permissions or inactive timers) do not trigger failure.

```bash
# Run local diagnostic checks:
rh-support-cli doctor

# Output in plain text (for scripts or CI):
rh-support-cli doctor --simple-output

# Machine-readable JSON summary:
rh-support-cli doctor --json

# Run with online connectivity probe:
rh-support-cli doctor --online
```

### 5.18 Preserved Comment Drafts (`drafts`)

Manage drafts preserved when an editing or posting session was interrupted:

* **Storage Location**: Drafts are stored in `$XDG_CONFIG_HOME/rh-support-cli/drafts/` (or configured via `drafts_dir` in `config.yaml` / `RH_SUPPORT_DRAFTS`).
* **Draft Matching**: `drafts show` selects by exact filename first, then by case number. If multiple drafts match, specify the exact filename.
* **Cleanup Safety**: `drafts clear` skips and reports active drafts locked by another process or files modified since reading.
* Running `comment -c <case>` automatically discovers and resumes the latest draft for that case.

```bash
# List saved drafts:
rh-support-cli drafts

# Filter drafts by case:
rh-support-cli drafts list -c 12345

# Preview draft content:
rh-support-cli drafts show 12345

# Clear drafts for a specific case:
rh-support-cli drafts clear -c 12345 -y

# Clear all saved drafts:
rh-support-cli drafts clear -y
```

---

## 6. Interactive TUI Dashboard (`tui`)

Launch an interactive split-pane terminal dashboard:

```bash
rh-support-cli tui
```

### TUI Options:
* `-l <layout>` / `--layout <layout>`: Layout orientation (`horizontal` / mailbox default, or `vertical` side-by-side).
* `-b <name>` / `--bookmark <name>`: Launch with a specific bookmark filter.
* `--order <newest|oldest>`: Comment ordering on startup.
* `--no-default-bookmark`: Launch unfiltered.
* `--no-cache`: Bypass disk cache on startup.

### Keyboard Shortcuts:

| Key | Action |
| :--- | :--- |
| **`Tab`** / **`Shift+Tab`** | Toggle focus between case list and details pane |
| **`j` / `k` / `Up/Down`** | Navigate row-by-row or scroll lines |
| **`PageUp` / `PageDown`** | Scroll screen-by-screen |
| **`c`** | Open inline comment drafting pane |
| **`Ctrl+X`** | Exit comment composer (prompts to Save/Discard/Cancel if dirty) |
| **`o`** | Toggle comment ordering (newest first vs oldest first) |
| **`g`** / **`:`** | Jump directly to case by number |
| **`/`** / **`s`** | Search and filter displayed queue (Search Modal) |
| **`K`** / **`?`** | Search Knowledgebase / KCS solutions (KCS Modal) |
| **`a`** | Upload file attachment (Attach Modal) |
| **`d`** | Download selected attachment |
| **`t`** | Apply template to selected case (Template Modal) |
| **`b`** | Switch active filter bookmark (Bookmark Modal) |
| **`v` / `l`** | Toggle horizontal / vertical split layout |
| **`w`** | Toggle follow/unfollow for selected case |
| **`m`** | Mark selected case as read |
| **`f`** | Toggle fullscreen zoom for active pane |
| **`r`** | Refresh active pane |
| **`q`** | Quit TUI |

### Navigation & Modal Dialogs
* **Search Modal (`/` or `s`)**: Filters the active table by keyword, summary, or case number. Press `Escape` to clear search and restore the regular case queue.
* **Knowledgebase / KCS Modal (`K` or `?`)**: Opens KCS search pre-filled with the active ticket summary. Solutions can be opened in a browser or inserted directly into the comment composer.
* **Read Marking & Dwell**: Selecting a case marks it as read after a dwell time, or press `m` to mark it read immediately.
* **Network & View Retention**: If a background refresh fails, the TUI displays a warning and retains the current view and any active comment draft.

---

## 7. Stateful Offline Mock Mode (`--mock` & `mock-portal`)

Run the CLI and TUI offline against an in-process or standalone mock portal:

### In-Process Mock (`--mock`)
```bash
rh-support-cli --mock list
rh-support-cli --mock show -c 1001 --no-pager
rh-support-cli --mock tui
```

### Standalone Mock Portal (`mock-portal`)
```bash
rh-support-cli mock-portal --port 8080
export RH_API_URL=http://localhost:8080
export RH_SSO_URL=http://localhost:8080/auth/token
export REDHAT_SUPPORT_OFFLINE_TOKEN=cli_token
```
