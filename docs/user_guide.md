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

# Default comment display order ('newest' or 'oldest', default: newest)
comments_order: newest
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

### Custom Configuration File (`--config-file` / `RH_SUPPORT_CONFIG`)
An explicitly selected missing file, an unreadable existing file, invalid YAML/known field types, or an unknown selected bookmark stops the command with a nonzero diagnostic before API activity. An absent optional default config is still valid. Selected paths expand `~`. Empty bookmarks are valid mappings; use `{}` for an empty configuration rather than a null or nonmapping YAML root.

CLI explicit bookmarks overlay default fields unless `--no-default-bookmark` is set. Selecting a TUI bookmark replaces its default selection, and the TUI clear option remains unfiltered. These selection rules are unchanged; misspelled names no longer silently broaden scope.

You can specify an alternate configuration file using `--config-file` or the `RH_SUPPORT_CONFIG` environment variable:
```bash
rh-support-cli --config-file /path/to/custom_config.yaml list
```

### Authentication And Editing
Editor commands honor quoted arguments in `$EDITOR` consistently for comments and descriptions. A failed launch, nonzero editor exit, or unreadable draft stops submission with a nonzero error and retains the recovery file. Empty file-fed or edited comments/descriptions cannot create comments, cases, or status changes; generated recovery drafts are retained on failure. Explicit cancellation remains cancellation, not a failed API operation.

Authentication failures produce a nonzero CLI error without printing raw SSO responses. An explicitly selected empty or unreadable credential does not silently fall back to a different source. Library consumers receive exceptions instead of a process exit. Automatic authentication retries preserve seekable body offsets; a non-replayable body fails rather than resubmitting empty content.

### Debugging & HTTP Logging (`--debug` / `--debug-file`)
Inspect network communication and API payloads:
* `--debug`: Prints redacted HTTP requests and responses directly to `stderr`.
* `--debug-file <path>`: Logs full, untruncated redacted JSON requests and non-streamed responses to the specified file while keeping console output clean.
* Streamed downloads are logged as status and headers only, without reading the downloaded body. Credentials, cookies, and signed URLs are redacted; opaque non-JSON request bodies are omitted. Logs can still contain case descriptions and other diagnostic content, so handle them as sensitive files.

```bash
# Output debug logs to terminal:
rh-support-cli --debug list

# Log full HTTP payload exchanges to a file:
rh-support-cli --debug-file /tmp/rh_debug.log show -c 12345678
```

### Read Failures
For commands supporting structured output, successful stdout contains only the requested JSON/YAML result. Empty `updates` results, including an untracked requested case, produce `[]`. Warnings and progress go to stderr; failures exit nonzero without a partial structured payload. `apply --json` / `--format yaml` emits a plan/result containing `case_number`, `dry_run`, `changes`, and `applied`; no-op plans have empty changes and dry runs never mutate. Template listing skips invalid metadata with a stderr warning while preserving a parseable list of valid entries.

Unavailable case histories, KCS searches, and attachment lists produce nonzero errors rather than successful empty results. A missing case is reported separately from an unavailable read. Failed structured queries leave stdout empty and put diagnostics on stderr.

The TUI retains its last complete view and draft and shows a warning. If a newly selected case cannot load, the retained view still identifies the old case; detail actions require a matching loaded selection. Failed refreshes cancel automatic read marking and prevent manual acknowledgement until data is successfully retrieved or revalidated. Optional editor context is omitted with a warning if history retrieval fails; authentication failures still stop submission.

Sync preserves successful updates even when other tracked cases are unavailable, reports incomplete sync with a nonzero exit, and does not change failed cases or advance read checkpoints.

### Global Cache Control (`--no-cache`)
Search honors an explicit zero limit as an empty result rather than replacing it with the default limit.

Global JSON/YAML output, simple-output, and no-cache flags retain their values before or after supported subcommands, including nested `attachment` and `attachments` actions. For create/comment/close, command-level `--format` still selects content formatting (`markdown`, `plain`, `html`, or `raw`), not result serialization.

Comment limits are honored on cached and fresh results. A cached partial thread cannot satisfy a full-thread request or appear as the instant full TUI view. Previous-comment editor context selects the latest requested comments rather than the beginning of a long thread.

Product-scoped bookmarks and combined account/status/severity/owner filters also apply to followed-case lists in the CLI and TUI. Combined case/KCS search keeps case-specific filters on its case results. Locally cached case search honors owner restrictions as well.

Private case lists and threads are isolated by the selected credential and backend. Older unscoped case caches are not reused; fetch cases again to populate the isolated cache. Changing an offline token selects a new cache, even if it belongs to the same user.

`search --local` performs no authentication or network requests. It requires a locally readable offline credential (via `--token-file`, `REDHAT_SUPPORT_OFFLINE_TOKEN`, or the standard token file) to select its cache. Without one, it exits with an actionable error instead of searching unrelated cached data. A valid cached access token is not required.

Local mode searches case caches only. `search --local --type all` leaves KCS results empty without a remote search; `--local --type kcs` is rejected before authentication or network access.

To bypass reading from or writing to the local disk cache, pass `--no-cache` or set `export RH_NO_CACHE=1`:
```bash
rh-support-cli list --no-cache
rh-support-cli show -c 12345678 --no-cache
```

### Environment Variables Reference

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `REDHAT_SUPPORT_OFFLINE_TOKEN` | Red Hat SSO offline API token | None (prompted/resolved) |
| `RH_SUPPORT_CONFIG` | Path to custom YAML configuration file | `~/.config/rh-support-cli/config.yaml` |
| `RH_SUPPORT_STATE` | Path to persistent local operational state | `~/.local/state/rh-support-cli/state.yaml` |
| `RH_NO_CACHE` | Set to `1` or `true` to bypass local disk cache globally | `0` |
| `RH_GRAPHQL_URL` | Override CRM GraphQL API endpoint | `https://graphql.redhat.com` |
| `RH_ATTACHMENT_API_URL` | Override v3 Attachment API endpoint | `https://api.access.redhat.com/support` |
| `RH_KCS_SEARCH_URL` | Override Hydra KCS search endpoint | `https://access.redhat.com/hydra/rest/search/kcs` |
| `RH_SSO_URL` | Override Red Hat SSO OAuth token endpoint | `https://sso.redhat.com/...` |
| `EDITOR` | Terminal text editor for comments and descriptions | `vi` |
| `FORCE_COLOR` | Set to `1` to force ANSI colors when redirected or piped | `auto` |

State changes use an empty `.lock` sidecar beside the selected state file so concurrent CLI, TUI, and background sync updates do not overwrite one another. Leave the sidecar in place while clients may be running. Ordinary state reads do not require write access.

---

## 4. Case Sizing Templates

Templates are Jinja2-compatible YAML files stored in `~/.config/rh-support-cli/templates/`. They allow pre-filling case values during creation (`create`) or updating existing cases into compliance (`apply`).

When `templates_dir` is configured, it is also used by comment/close templates and the TUI template selector. Bookmark/template completion respects an explicit global `--config-file`. Configured cache TTLs apply consistently to CLI and TUI requests.

Explicit template requests fail closed: missing files/includes, circular includes, invalid YAML structure, undefined variables, or rendering errors produce a nonzero exit before any case mutation. Template roots must be mappings with string field names; `include_templates` must be a name or list of names. Provide required values with `--template-var name=value`. For optional values, use an explicit default such as `{{ optional_value | default('fallback') }}`. A comment/close template rendering an empty body cannot submit a status change by itself; provide comment text, a content file, or use `--edit`.

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

# Filter to cases you are locally following:
rh-support-cli list --following

# List high severity cases for an account:
rh-support-cli list --account 12345 --status "Waiting on Red Hat" --severity High

# Filter using an explicit bookmark (or bypass default bookmark):
rh-support-cli list -b my-rhel
rh-support-cli list --no-default-bookmark

# Fetch all cases across all pages:
rh-support-cli list --status open --all

# Limit number of cases returned:
rh-support-cli list --limit 50

### 5.2 Showing Case Details (`show`)
Displays complete case metadata, descriptions, attachments, and the comment thread (piped to `$PAGER` by default):

```bash
# View case details (newest comments first by default, matching portal):
rh-support-cli show -c 12345678

# View in chronological order (oldest comments first):
rh-support-cli show -c 12345678 --oldest-first
# or:
rh-support-cli show -c 12345678 --order oldest
# Print directly to stdout without pager:
rh-support-cli show -c 12345678 --no-pager
# Mark unread updates on this case as read:
rh-support-cli show -c 12345678 --mark-read

# Export incident shift-handoff summary (Markdown, Slack, or JSON):
rh-support-cli show -c 12345678 --export
rh-support-cli show -c 12345678 --export slack
rh-support-cli show -c 12345678 --export json
```

### 5.3 Case Browser Link (`link`)
Generates the browser URL for a support case (bypasses authentication):
```bash
rh-support-cli link -c 12345678
# Output: https://access.redhat.com/support/cases/12345678
```

### 5.4 Creating Cases (`create`)
Creation stops before mutation if contact, account, record-type, or product lookups are missing, malformed, or ambiguous. An explicit account must match the resolved contact's account; no account or entity IDs are guessed. If the access token does not provide an SSO username, set `REDHAT_SUPPORT_USERNAME` to your Red Hat SSO login (library callers can pass `sso_user`). Your OS username is not used for creation.

Until the production version-filter contract is verified, the name lookup must return one product whose reported name and version exactly match the request. Other results fail with a validation error rather than creating a case under a potentially wrong version. Lookup failure preserves editor-generated descriptions and does not start attachment uploads.

Creates a new support ticket interactively or via command-line flags:

Creation always asks for confirmation unless you explicitly pass `create --yes`. With `--yes`, supply nonempty product, version, summary, severity, type, and description through flags or templates; missing values fail without prompts or an editor. Supply the description through `--description-file` or a template. JSON output alone never bypasses confirmation.

Templates may use numeric YAML versions (such as `version: 9.0`) and integer severity codes (`1` urgent, `2` high, `3` normal, `4` low), including with `--yes`. If validation fails, check the product/version, required text, attachment readability, and contact/account/record-type lookup settings indicated on stderr. Set `REDHAT_SUPPORT_USERNAME` if SSO identity cannot be resolved; verify the Customer Portal before retrying creation.

Use `create --json` (or global `--format json` before `create`) for one JSON receipt on stdout; global `--format yaml` emits the same receipt as YAML. The command-level `create --format` controls description content only. Prompts, overviews, progress, and errors go to stderr. Receipts contain `case_number`, `created`, `success`, and per-file `attachments` outcomes (`file`, `uploaded`, `attachment_id`, `error`). A created case with any unconfirmed attachment produces a receipt with `created: true`, `success: false` and a nonzero exit status. Do not repeat creation: check that existing case before retrying uploads. Description drafts are removed after confirmed creation even if attachments fail, since the description has already been submitted. A creation failure preserves generated drafts; if the creation outcome is unknown, check the Customer Portal before retrying.

```bash
# Interactive mode (prompts for product, version, summary, severity):
rh-support-cli create

# Non-interactive mode with a JSON receipt:
rh-support-cli create --yes --json \
  --product "Red Hat Enterprise Linux" \
  --version "9.0" \
  --summary "Kernel panic on boot" \
  --description-file ./panic.log \
  --severity "High" \
  --type "Bug" \
  --attachment /tmp/sosreport.tar.xz

# Create from template:
rh-support-cli create --template proactive_upgrade --template-var cluster="OCP-Prod"

# Ignore default create template defined in config:
rh-support-cli create --no-default-template
```

### 5.5 Commenting & Status Updates (`comment`)
Posts a comment to an existing case and optionally transitions its status (defaults to `Waiting on Red Hat`):

If the comment is posted but the status update is not confirmed, the command exits nonzero and tells you to check the case and change only its status. Do not repost the comment. Generated editor drafts are removed after confirmed posting, even on a later status failure; any cleanup warning identifies a submitted copy. An unknown posting outcome preserves a recovery copy and requires checking the portal before retrying. The TUI clears the submitted body on partial success while retaining any newer draft and displaying the recovery warning.

`comment --json` and `close --json` emit one receipt with `case_number`, `comment_id`, `comment_posted`, `requested_status`, `status_confirmed`, and `success`. Global `--format yaml` emits the same receipt as YAML; command-level `--format` still controls comment content. Progress/errors go to stderr. Partial success emits a receipt with `success: false` and exits nonzero; unconfirmed posting emits no success receipt.

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
# Interactively draft closing comment in $EDITOR (includes context from previous comments):
rh-support-cli close -c 12345678 --include-previous-comments 1

# Close case with comment from file (optionally edit before submitting):
rh-support-cli close -c 12345678 -f resolution.txt --edit

# Close case using a template:
rh-support-cli close -c 12345678 -t resolve_incident --template-var root_cause="DNS timeout"

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
Uploads validate the strategy and multipart manifest before transferring data, and verify expected byte counts before reporting success. Keep the source archive unchanged during upload. Normal transfers stream bounded chunks; library callers explicitly requesting a read-all operation can still allocate an entire file slice.

Downloads reject unsafe remote filenames and never overwrite an existing destination, including symlinks. If the output path already exists, choose another `-o` filename. The final file becomes visible only after the transfer completes; failed or interrupted transfers remove their temporary files.

Manage diagnostic archives and log bundles attached to support cases:

```bash
# Upload an attachment (shortcut):
rh-support-cli attach -c 12345678 -f /tmp/sosreport.tar.xz

# Upload via attachment subcommand (supports -c after or before upload, or positional case):
rh-support-cli attachment upload -c 12345678 -f /tmp/sosreport.tar.xz -d "Post-OOM report"
rh-support-cli attachment upload 12345678 -f /tmp/sosreport.tar.xz
rh-support-cli attachment -c 12345678 upload -f /tmp/sosreport.tar.xz
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
* **Low-Memory Streaming**: Large files stream through bounded reads (up to 8 MiB per block) rather than buffering the whole attachment. Total process memory depends on the runtime, interface, and enabled features; there is no fixed process-wide RAM ceiling.
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

# Filter followed cases using bookmarks or user:
rh-support-cli following -b ocp-team
rh-support-cli following -u colleague_sso

# Sync latest status from account before listing followed cases:
rh-support-cli following --sync

# Unfollow cases:
rh-support-cli unfollow 12345678

### 5.10 Synchronizing Tracked Cases & Systemd Timer (`sync` / `install-systemd` / `systemd`)
Polls Red Hat's backend across all followed and owned cases in parallel to detect remote updates:

```bash
# Run manual synchronization across all tracked cases:
rh-support-cli sync

# Sync tracked cases for a specific account or user:
rh-support-cli sync --account 12345 -u myself@redhat.com

# Sync with desktop notifications on new comments or status transitions:
rh-support-cli sync --notify

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
# View summary of unread updates across all tracked cases:
rh-support-cli updates

# Filter updates to a specific case number:
rh-support-cli updates -c 12345678

# Inspect all tracked cases, including those without new updates:
rh-support-cli updates --all

# View full unread comment bodies:
rh-support-cli updates --full

# Mark all inspected updates as read:
rh-support-cli updates --mark-read

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
`apply` supports only `status`, `severity`, `summary`, and `description`. Other directives, including product/version and all watcher/notified-user fields, cause a nonzero exit before any mutation, even with `--dry-run`. Fields starting with `_` remain metadata. Non-null values must be nonempty text (numeric severity is also accepted); null values are left unchanged. Creation templates containing additional fields need a separate supported-fields-only template for `apply`. Following remains local tracking; this command does not manage portal watchers.

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
rh-support-cli --simple-output show -c 12345678
```

### 5.16 Shell Auto-Completion (`completion`)
Generates shell auto-completion for commands, options, and dynamic values:
```bash
# Print bash completion script:
rh-support-cli completion

# Enable tab-completion in your current shell:
eval "$(rh-support-cli completion)"

# Persist bash completion in ~/.bashrc:
echo 'eval "$(rh-support-cli completion)"' >> ~/.bashrc
```

#### Dynamic Context-Aware Completions
Pressing `<TAB>` automatically suggests context-relevant values:
* **Products (`-p` / `--product`)**: Consults local catalog cache (`~/.cache/rh-support-cli/catalog/products.json`), falling back to bundled enterprise products (e.g. `Red Hat OpenShift Container Platform`).
* **Statuses (`-s` / `--status`)**: Contextually suggests query filters (`open`, `closed`, `redhat`, `customer`, `in_progress`) on `list`/`search`, or update transitions (`none`, `keep`, `redhat`, `customer`, `closed`) on `comment`.
* **Severities (`--severity`)**: Suggests `Low`, `Normal`, `High`, `Urgent`, `1`, `2`, `3`, `4`.
* **Bookmarks (`-b` / `--bookmark`)**: Suggests bookmark names defined in your `config.yaml`.
* **Templates (`-t` / `--template`)**: Suggests local template names from `~/.config/rh-support-cli/templates/`.
* **Case Numbers (`-c` / `--case`)**: Suggests tracked, followed, and recently viewed case numbers from `~/.local/state/rh-support-cli/state.yaml`.
* **File Paths (`-f` / `-o` / `--token-file`)**: Tab-completes local files and directories via `FilesCompleter`.

### 5.17 Environment & Health Diagnostics (`doctor`)
Runs automated local diagnostic health checks inspecting credentials, configuration, directory permissions, and background sync timers:

Cache write checks use a temporary file and leave existing cache files and symlinks untouched.
Authentication checks use the same source precedence as normal commands: `--token-file`, the `REDHAT_SUPPORT_OFFLINE_TOKEN` environment variable, then `~/.config/rh-support-cli/token`. An empty or unreadable selected source fails without falling back to another identity or prompting. Token values are fully masked; file permissions broader than `0600` produce a warning.
Doctor produces the full report even when configuration is invalid. A missing optional default config uses defaults (`INFO`); a missing file selected by `--config-file` or `RH_SUPPORT_CONFIG`, invalid YAML, or invalid settings reports `FAIL`. Any failed check makes doctor exit with status 1; warnings alone do not.
Storage diagnostics follow the same paths as normal commands, including `RH_SUPPORT_STATE`, state beside an explicitly selected config, and XDG defaults. Tilde and relative paths are supported. Cache and product-catalog checks use the application's cache location, including when `XDG_CACHE_HOME` already ends in `rh-support-cli`.

Doctor is offline by default. `--online` sends one unauthenticated `HEAD` probe to the configured GraphQL endpoint with a 3-second request timeout. Any HTTP response, including `401`, `403`, `405`, or a server error, proves HTTP reachability and reports `PASS` with the response code. DNS, TLS, connection, and timeout errors report `FAIL`. This does not check SSO, validate credentials, query cases, or establish API service health.

The systemd user-timer check also has a 3-second timeout. A timeout reports `WARN` with unknown timer status, rather than failing or blocking the report. An inactive or uninstalled timer is a warning; a host without `systemctl` reports `INFO`.

```bash
# Run local diagnostic health checks:
rh-support-cli doctor

# Output in plain text (for scripts or CI):
rh-support-cli doctor --simple-output

# Machine-readable JSON summary:
rh-support-cli doctor --json

# Perform optional bounded online connectivity probe:
rh-support-cli doctor --online
```

### 5.18 Comment Drafts Discovery & Recovery (`drafts`)
Generated editor boundaries are recognized only inside the leading comment header; marker text within the body remains content.
Retrying a generated recovery file with `comment --file` removes its editor guidance and previous-comment context. Ordinary input files and raw TUI drafts preserve leading Markdown headings, including during automatic resume.
`drafts show` prefers an exact filename, then an exact case number (ignoring leading zeroes), then a unique partial filename. Multiple matches fail with an ambiguity error; use the full filename to select one.
Listing and automatic resume order drafts by full filesystem modification precision, not the rounded timestamp displayed in the table. JSON includes `mtime_ns`.
CLI editing, TUI saving, and draft discovery use the same directory: the string setting `drafts_dir`, then `RH_SUPPORT_DRAFTS`, then `drafts` beside `RH_SUPPORT_CONFIG`, then `$XDG_CONFIG_HOME/rh-support-cli/drafts`, falling back to `~/.config/rh-support-cli/drafts`.
Discovery ignores symlinks, nonregular files, files owned by another user, and entries that disappear or change during reading. Cleanup checks the captured file identity again after confirmation and preserves changed/replaced files.
Confirmed posting retires both the automatically resumed source and the new editor copy, including when a later status change fails. Copies changed since reading/submission remain recoverable. Failed or unknown posts retain both copies; check the portal before retrying an unknown outcome.
Cleanup skips and reports active drafts, including their resumed sources, even if an editor saves by atomic replacement. Ownership is rechecked after confirmation; another CLI cannot resume a draft already in use. Lock contention fails promptly rather than waiting. Small hidden/sidecar lock files remain in storage and are not drafts.

Discovers, previews, and purges preserved comment drafts stored when editing or posting was interrupted:

```bash
# List all saved comment drafts:
rh-support-cli drafts

# Filter drafts by case number:
rh-support-cli drafts list -c 12345

# Preview a draft's full content by filename or case number:
rh-support-cli drafts show 12345

# Delete drafts for a specific case:
rh-support-cli drafts clear -c 12345 -y

# Purge all drafts:
rh-support-cli drafts clear -y
```

When invoking `rh-support-cli comment -c <case>` interactively, any previously preserved draft for that case is automatically discovered and resumed in your `$EDITOR`.
## 6. Interactive TUI Mode (`tui`)

Launch an interactive split-pane terminal dashboard:

```bash
rh-support-cli tui
```

Automatic refresh continues when caching is disabled or cached details expire. Empty queues clear the old rows, and obsolete refreshes cannot replace the current filter or selection. An open draft is preserved while case details refresh.

### Status Bar & Header Chips:
The top status bar provides real-time, non-blocking operational context:
* **Identity Chip (`User: <name>`)**: Shows the authenticated SSO username or local user.
* **Scope Chip (`Bookmark: <name>` or `Search: "<query>"`)**: Displays the currently active filter bookmark or search query. Updates immediately upon bookmark selection (`b`) or case search (`/`), restoring to `Bookmark: None` when search is cleared (`Esc`).
* **Freshness Chip (`Refreshed: <time>`)**: Displays the local timestamp of the last successful case list synchronization (via manual refresh or background polling).
### Options:
* `-l <layout>` / `--layout <layout>`: Split layout (`horizontal` / `mailbox` default or `vertical` side-by-side).
* `-b <name>` / `--bookmark <name>`: Launch with a specific bookmark filter.
* `--newest-first` / `--order <newest|oldest>`: Comment display ordering on startup (newest or oldest first).
* `--no-default-bookmark`: Launch unfiltered.
* `--no-cache`: Bypass disk cache on startup.
### Keyboard Controls:
| Key | Action |
| :--- | :--- |
| **`Tab`** / **`Shift+Tab`** | Toggle focus between case list and details pane |
| **`j` / `k` / `Up/Down`** | Navigate row-by-row / scroll lines |
| **`PageUp` / `PageDown`** | Scroll screen-by-screen |
| **`c`** | Open inline Comment drafting pane |
| **`Ctrl+X`** | Close the composer, checking Save/Discard/Cancel for a nonempty draft |
| **`o`** | Toggle comment ordering (newest first vs oldest first) |
| **`g`** / **`:`** | Jump directly to case by number (Jump Modal) |
| **`/`** / **`s`** | Search support cases by keywords / filter queue (Search Modal) |
| **`K`** / **`?`** | Search Red Hat Knowledgebase / KCS solutions (KCS Modal) |
| **`a`** | Upload file attachment (Attach Modal) |
| **`t`** | Select and apply template (Template Modal) |
| **`b`** | Switch active filter bookmark (Bookmark Modal) |
| **`v` / `l`** | Toggle horizontal/vertical layout |
| **`w`** | Toggle follow/unfollow for active case |
| **`m`** | Mark active case as read |
| **`f`** | Zoom / Fullscreen toggle for active pane |
| **`r`** | Contextual refresh |
| **`q`** | Quit TUI |

Bookmark/template dialogs can open without a preselected option; confirming an empty selection makes no change. Pressing `/` or `s` opens the case search dialog to filter the displayed table by query keywords, summary, or case number; pressing `Escape` clears an active search and restores the regular case queue. Pressing `K` or `?` opens the Red Hat Knowledgebase (KCS) search dialog pre-filled with the active case summary, allowing you to open solutions in your browser or insert solution links directly into your comment draft. The documented `b`, `f`, `o`, `g`, `s`, and `K` keys operate outside text entry, while typing those letters in the comment editor remains ordinary input. Order changes use the loaded thread without another request. Automatic read marking applies only after the current displayed version's dwell period; navigation or a new detail version supersedes older timers.

---

KCS searches run without blocking the dashboard. Authentication or service failures appear as explicit failures, not as "no solutions found"; submit the query again to retry. A newer query supersedes older results, and closing the dialog discards pending results.

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
