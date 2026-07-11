# Red Hat Support Case CLI

A command-line interface tool to interact with the Red Hat Support Case API. This tool allows you to create new cases, attach files, and post comments/updates to existing cases directly from your terminal.

## Installation

You can install the tool and its dependencies using `pip`. This will install the `rh-support-cli` command to your system (e.g., `/usr/local/bin`) and the library files to your Python site-packages.

```bash
# Install from source
pip install .

# Or for development (editable mode)
pip install -e .
```

### Dependencies
The following Python packages are required (automatically installed via pip):
- `requests`
- `pyyaml`
- `jinja2`
- `dateparser`

## Authentication

The tool requires a Red Hat Offline Token to authenticate. You can obtain this from the [Red Hat Customer Portal](https://access.redhat.com/articles/3626371#bgenerating-a-new-offline-tokenb-3).

The tool looks for the token in the following order of priority:

1.  **CLI Argument:** Pass the path to a file containing the token using `--token-file`.
    ```bash
    rh-support-cli --token-file /path/to/token.txt list
    ```
2.  **Environment Variable:** Set `REDHAT_SUPPORT_OFFLINE_TOKEN`.
    ```bash
    export REDHAT_SUPPORT_OFFLINE_TOKEN="your_offline_token_here"
    ```
3.  **Config File:** Place the token in `~/.config/rh-support-cli/token`.
    ```bash
    mkdir -p ~/.config/rh-support-cli
    echo "your_offline_token_here" > ~/.config/rh-support-cli/token
    chmod 600 ~/.config/rh-support-cli/token
    ```
4.  **Interactive Prompt:** If none of the above are found, the tool will securely prompt you for the token.

## Usage

If installed via pip:
```bash
rh-support-cli [global options] <subcommand> [options]
```

Or running the script directly:
```bash
rh-support-cli [global options] <subcommand> [options]
```

**Global Options:**
- `--token-file`: Path to file containing the offline token.
- `--simple-output`: Disable colored output and visual indicators.

### 1. List Cases (`list`)

Lists support cases with options to filter by account, status, severity, and owner.

**Options:**
- `--account`: Account Number
- `--status`: Filter by status (can be used multiple times)
- `--severity`: Filter by severity (can be used multiple times)
- `--owner`: Filter by owner
- `--bookmark`: Use a filter bookmark from config (can be used multiple times)
- `--no-default-bookmark`: Ignore default bookmark from config

**Examples:**

*List all cases:*
```bash
rh-support-cli list
```

*List high severity cases for a specific account:*
```bash
rh-support-cli list --account 12345 --status "Waiting on Red Hat" --severity High
```

### 2. Show Case Details (`show`)

Displays detailed information about a case, including description and comments. Output is piped to a pager by default.

**Options:**
- `-c`, `--case`: The Case Number (required)
- `--no-pager`: Disable the pager (print to stdout directly)

**Example:**
```bash
rh-support-cli show -c 12345678
```

### 3. Get Case Link (`link`)

Generates the browser URL for a specific support case. This command does not require authentication.

**Options:**
- `-c`, `--case`: The Case Number (required)

**Example:**
```bash
rh-support-cli link -c 12345678
# Output: https://access.redhat.com/support/cases/12345678
```

### 4. Create a New Case (`create`)

Creates a new support case. If options are omitted, the tool will interactively prompt you for the necessary information, fetching available products and versions from the API.

**Options:**
- `--product`: Product name (e.g., "Red Hat Enterprise Linux")
- `--version`: Product version (e.g., "8.4")
- `--summary`: Short summary of the issue
- `--description-file`: Path to a file containing the detailed description. If omitted, opens your default `$EDITOR`.
- `--severity`: Case severity (Low, Normal, High, Urgent)
- `--type`: Case type (Standard, Bug)
- `--attachment`: Path to a file to attach. Can be used multiple times.
- `--no-default-template`: Ignore default create template from config.

**Examples:**

*Interactive Mode:*
```bash
rh-support-cli create
```

*Non-Interactive Mode:*
```bash
rh-support-cli create \
  --product "Red Hat Enterprise Linux" \
  --version "8.6" \
  --summary "Server crash on boot" \
  --description-file ./crash_log.txt \
  --severity "High" \
  --type "Bug" \
  --attachment /var/log/messages \
  --attachment /var/log/dmesg
```

### 5. Attach a File (`attach`)

Uploads a file to an existing support case.

**Options:**
- `-c`, `--case`: The Case Number (required)
- `-f`, `--file`: Path to the file to attach (required)

**Example:**
```bash
rh-support-cli attach -c 12345678 -f /tmp/sosreport.tar.xz
```

### 6. Comment & Update Status (`comment`)

Adds a comment to a case and optionally updates its status.

**Options:**
- `-c`, `--case`: The Case Number (required)
- `-f`, `--file`: Path to a file containing the comment text. If omitted, opens `$EDITOR`.
- `-s`, `--status`: Update case status. Choices: `redhat` (Waiting on Red Hat), `customer` (Waiting on Customer), `closed`. Default: `redhat`.

**Examples:**

*Add comment from a file and set status to "Waiting on Red Hat":*
```bash
rh-support-cli comment -c 12345678 -f response.txt
```

*Open editor to write comment and close the case:*
```bash
rh-support-cli comment -c 12345678 -s closed
```

### 7. Apply Template & Watchers (`apply`)

Applies a local YAML template over an existing support case. It compares the template fields and watchers/notified users with the case's current values, and performs the minimal required API requests to bring the case to the target state.

**Options:**
- `-c`, `--case`: The Case Number (required)
- `-t`, `--template`: Name of local template to apply (required, can be used multiple times)
- `--template-var`: Variables to interpolate in the template (can be used multiple times)
- `--dry-run`: Show what changes would be applied without actually making them

**Examples:**

*Verify template application without saving (Dry Run):*
```bash
rh-support-cli apply -c 12345678 -t default-watchers --dry-run
```

*Apply template and interpolate template variables:*
```bash
rh-support-cli apply -c 12345678 -t project-team --template-var team_lead=alice
```

### 8. List Local Templates (`list-templates` / `list-template`)

Lists all local templates stored in `~/.config/rh-support-cli/templates/` along with details like their description, fields they configure, parent templates they include, and watchers they would add. **Note:** This command runs completely offline and bypasses authentication.

**Example:**
```bash
rh-support-cli list-templates
```

### 9. Interactive Terminal User Interface (`tui`)

Launches a beautiful, interactive Terminal User Interface (TUI) to browse and manage your Red Hat support cases directly inside your terminal session.

**Options:**
- `-b`, `--bookmark`: Use a preset bookmark of filters on startup instead of the default bookmark
- `--no-default-bookmark`: Do not load any default bookmark on startup (loads all cases unfiltered)

**Features:**
- **Split Dashboard Layout**: Scroll through the list of cases in the left pane while viewing details, description (fully parsed Markdown), and complete comment threads in the right pane.
- **Asynchronous Background Processing**: Keeps the UI highly responsive by loading cases and updates asynchronously.
- **Vim / Arrow Navigation**: Navigate intuitively with standard keyboard binds.
- **Single-Pane Focus / Zoom**:
  - Highlight either the case list on the left or case details on the right, and press **`f`** to "zoom" that panel into fullscreen mode (fully hiding the other pane to maximize reading space).
  - **Intuitive Toggling**: Press **`f`** again or press **`Escape`** to instantly exit single-pane mode and restore the standard dual-pane split dashboard layout!
- **Quick Modals & Action Buttons**:
  - Press **`c`** (or click `Comment (C)`) to open the inline **Comment Pane** at the bottom of your case view, allowing you to scroll through and read the case description and comment history while drafting. Includes status dropdown integration (pre-selecting `"Waiting on Red Hat"` if the case is `"Waiting on Customer"`) and "dirty-state" draft auto-saving under `~/.config/rh-support-cli/drafts/`.
  - Press **`a`** (or click `Attach (A)`) to easily specify and upload local file attachments in the background.
  - Press **`t`** (or click `Template (T)`) to select and apply a local template (e.g. standard watchers) to the active case.
  - Press **`b`** (or click `Bookmark (B)`) to select and apply any filter bookmark dynamically to refresh the case list, or clear filters entirely.
  - Press **`r`** (or click `Refresh (R)`) to trigger a contextual refresh (refreshes the active case view if the details panel is active; refreshes the case list if the table panel is active).

**Examples:**

*Launch TUI with default bookmark filters:*
```bash
rh-support-cli tui
```

*Launch TUI with a specific bookmark override:*
```bash
rh-support-cli tui -b my_cases
```

*Launch TUI unfiltered (all cases):*
```bash
rh-support-cli tui --no-default-bookmark
```

## Configuration & Bookmarks

You can define bookmarks (presets of filters) in a YAML configuration file.

**File Location:**
1.  Default: `~/.config/rh-support-cli/config.yaml`
2.  Environment Variable: `RH_SUPPORT_CONFIG`
3.  CLI Argument: `--config-file <path>`

**Example `config.yaml`:**
```yaml
default_bookmark: "my_team"
default_create_template: "base_openshift"
debug_file: "/path/to/debug.log"  # Log all API requests/responses in background

bookmarks:
  my_team:
    account: "12345678"
    status: ["redhat", "customer"]
    severity: ["High", "Urgent"]
  
  my_cases:
    owner: "jdoe"
    status: ["open"]
```

**Usage:**

*   **Use Default:** If `default_bookmark` is set, it applies automatically to `list`.
*   **Use Specific:** `rh-support-cli list --bookmark my_cases`
*   **Combine:** `rh-support-cli list --bookmark my_team --bookmark my_cases` (Later bookmarks override earlier ones)
*   **Disable Default:** `rh-support-cli list --no-default-bookmark`
*   **Override:** Explicit CLI flags override bookmarks.
    ```bash
    # Uses 'my_team' account but overrides status to 'Closed'
    rh-support-cli list --status Closed
    ```

## Templates

You can use templates to pre-fill case details during creation, or post-creation to verify and bring cases into compliance with standard fields and watchers. Templates are YAML files stored in `~/.config/rh-support-cli/templates/`.

**Structure:**
- Templates can define any case field (product, version, summary, description, severity, etc.).
- They can define lists of watchers / notified users to be added (using `watchers`, `notified_users`, or `notifiedUsers` keys).
- `include_templates`: List of other templates to include (merged in order).
- Values are **Jinja2** templates.
- `currentDoc`: Available in Jinja context, representing the merged data so far.
- CLI Variables: Pass variables using `--template-var key=value`.

**Example:**
`~/.config/rh-support-cli/templates/base_openshift.yaml`:
```yaml
product: "OpenShift Container Platform"
version: "4.12"
severity: "3 (Normal)"
caseType: "Bug"
```

`~/.config/rh-support-cli/templates/proactive.yaml`:
```yaml
include_templates: ["base_openshift"]
caseType: "Other"
severity: "4 (Low)"
summary: "[Proactive] {{ product }} {{ version }} update on {{ cluster_name }}"
description: |
  Planning upgrade to {{ next_version }}.
  ClusterID: {{ cluster_id }}
watchers:
  - alice
  - bob
```

**Usage:**

*For Case Creation (`create`):*
```bash
rh-support-cli create \
  --template proactive \
  --template-var cluster_name=Prod1 \
  --template-var next_version=4.13 \
  --template-var cluster_id=12345
```

**Requirements:**
- `jinja2`
- `dateparser` (optional, for `parse_date` filter)
```bash
pip install jinja2 dateparser
```

## Bash Completion

This tool supports Bash completion via `argcomplete`.

1.  Ensure `argcomplete` is installed:
    ```bash
    pip install argcomplete
    ```
2.  Activate completion:
    ```bash
    eval "$(rh-support-cli completion)"
    ```
    Add this line to your `~/.bashrc` or `~/.zshrc` for persistent completion.

### API URLs

The following environment variables can be used to override default API URLs (useful for testing or pointing to different environments):

- `RH_API_URL`: Base URL for the Support API (Default: `https://api.access.redhat.com/support/v1`)
- `RH_SSO_URL`: URL for the SSO Token endpoint (Default: `https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token`)

## API Documentation

For more details on the available endpoints and data structures, you can consult the official Red Hat Support Case API Swagger documentation:

[Red Hat Support Case API Swagger (JSON)](https://access.redhat.com/management/api/case_management/swagger.json) (Authentication required)

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

## Testing

The project includes a comprehensive test suite that uses a built-in mock server to simulate the Red Hat API. This allows you to run tests without needing real credentials or network access to the actual API.

To run the tests:

```bash
python3 test_rh_support_cli.py
```

The test suite covers:
- Authentication flow (including missing token handling)
- File attachments (success and error states)
- Commenting and status updates
- Case creation (verifying flags and attachment uploads)
