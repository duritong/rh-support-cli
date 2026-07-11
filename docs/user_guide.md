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

## 2. Configuration & Bookmarks

Configuration is stored in your user configuration folder: `~/.config/rh-support-cli/config.yaml`.

### Example `config.yaml`
```yaml
# Set your default template name for case creation or TUI pre-selection
default_create_template: base-template

# Set your default bookmark filters on case list startup
default_bookmark: ocp-team

# Set your background debug file for logging full redacted HTTP sessions
debug_file: ~/.config/rh-support-cli/debug_log.txt

# Group custom filter bookmarks
bookmarks:
  ocp-team:
    status: ["Waiting on Red Hat", "Waiting on Customer"]
    severity: ["High", "Urgent"]
    product: "OpenShift Container Platform"
```

---

## 3. Case Sizing Templates

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

---

## 4. Subcommands Usage

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

## 5. Interactive TUI Mode (`tui`)

Launch a full split-pane interactive dashboard to manage your cases directly inside your terminal:
```bash
rh-support-cli tui
```

### Core Features & Layout
*   **Dual-Pane View**: Case table on the left, full rich case details, descriptions (fully rendered Markdown), and complete scrollable comments thread on the right.
*   **Contextual Command Buttons**: Interactive action buttons (`Add Comment (C)`, `Apply Template (T)`, `Refresh (R)`) are rendered at the top of the details view for easy mouse-clicks or keyboard legends.
*   **Compact Scrollbars**: Universal stylesheet styles make all horizontal and vertical scrollbars a super-sleek 1-character-thin column with a transparent track background.

### Keyboard Navigation & Scrolling Controls
Toggle focus between the case list and details pane using **`Tab`** or **`Shift+Tab`**. When focused on a pane:

| Command | Action |
| --- | --- |
| **`j` / `k` / `Arrow Up/Down`** | Scroll or navigate row-by-row |
| **`PageUp` / `PageDown`** | Scroll screen-by-screen |
| **`Home` / `End`** | Jump to the very top or bottom of the viewport |
| **`c`** | Post a new comment to the selected case (Comment Modal) |
| **`t`** | Select and apply a local template (Template Modal) |
| **`r`** | Pull-to-refresh the active case list |
| **`q`** | Quit the TUI |

---

## 6. Remote VM Terminal Color Configurations (SSH + Tmux + Podman)

When running the TUI inside nested SSH sessions, TMUX multiplexers, and Podman containers, terminal colors or escape lines can get lost. Follow these settings to propagate them cleanly:

### 1. SSH Layer
Start SSH requesting a 256-color terminal:
```bash
TERM=xterm-256color ssh user@vm-host
```

### 2. Tmux Layer
Add this to your `~/.tmux.conf` on the VM to override and translate 24-bit RGB Truecolors:
```tmux
set -g default-terminal "screen-256color"
set -as terminal-overrides ",xterm-256color:Tc"
```
And launch `tmux` forcing 256 colors:
```bash
tmux -2
```

### 3. Podman Layer
Run Podman container allocating a TTY, passing your `TERM` environment variable, and mounting the host's terminfo database:
```bash
podman run --rm -it \
  -e TERM=$TERM \
  -v /usr/share/terminfo:/usr/share/terminfo:ro \
  your-image-name /bin/bash
```
