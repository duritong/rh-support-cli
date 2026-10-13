# Red Hat Support Case CLI - Core Vision & Guidelines

This document outlines the high-level vision, target audience, and core architectural design principles for the Red Hat Support Case CLI. It serves as a foundational guideline and a strategic anchor for all future feature designs and technical decisions.

---

## 1. High-Level Vision & Mission

The **Red Hat Support Case CLI** is a modern, developer-centric terminal workspace that bridges the gap between local system operations and the official Red Hat Customer Portal. 

### Core Mission:
> *To empower platform engineers, SREs, and systems administrators to troubleshoot, research, and coordinate Red Hat support tickets and technical solutions directly inside their natural shell environment with zero friction, lightning-fast response times, and powerful local automation.*

By centering ticket interactions and knowledge discovery inside the terminal, the tool enables operators to avoid the heavy load times and mouse-dependent workflows of standard web portals, seamlessly integrating support cases and Red Hat Knowledgebase (KCS) solutions into local debugging, scripting, and shell-based diagnosing sessions.

---

## 2. Target Audience & Personas

Understanding our users governs every usability and shortcut decision we make:

*   **The Cluster/Platform Engineer (OpenShift/RHEL)**: Manages dozens of multi-tenant clusters. Needs to research error signatures, open proactive case drafts, compile massive diagnostic logs (e.g. `must-gather`), and stream heavy bundles up to 5 TB instantly without context switching.
*   **The SRE / On-Call Operator**: Triages production incidents under high pressure. Needs a split-pane, dashboard-like terminal interface (TUI) running inside multiplexers (`tmux`/`screen`) to quickly browse case histories, search verified Red Hat solutions, filter status updates, and append rapid diagnostic findings.
*   **The Systems Automation Architect**: Builds cron scripts and Ansible/Bash playbooks. Consumes our underlying Python package as a standard CLI or library to programmatically audit case statuses, query technical solutions, or automatically enforce standard templates and notified watchers.

---

## 3. Core Architectural & Design Pillars

Every pull request and architectural decision must align with these five fundamental engineering pillars:

### Pillar I: Library-First Decoupling
*   **Principle**: The command-line parsing (`rh_support_cli.py`) and terminal user interface (`rh_support_lib/tui/`) must remain **completely thin wrapper layers** over a core reusable Python library (`rh_support_lib/`).
*   **Guideline**: Never embed API communication or config parsing logic directly inside CLI arguments or TUI event loops. If a feature is added to the TUI (such as uploading attachments, applying templates, or searching solutions), it must first exist as a clean, programmatic function in the core library, ensuring headlessly scriptable access.

### Pillar II: Resilient, Infinite-Session Authentication
*   **Principle**: Users expect the tool to run indefinitely inside terminal multiplexers without needing manually triggered re-authentications.
*   **Guideline**: Maintain a robust OIDC SSO client (`RedHatAPIClient`) that intercepts HTTP `401 Unauthorized` responses, transparently clears local cache directories, executes background token refreshes, and retries the target request in-process. Raw token strings should never be threaded statically.

### Pillar III: Thread-Safe, Responsive Asynchrony
*   **Principle**: The terminal user interface (TUI) and CLI must never freeze or become sluggish, even when streaming multi-gigabyte diagnostic archives or querying slow remote endpoints.
*   **Guideline**: All blocking I/O (such as network requests or file reads) must be offloaded to background threads. Any UI rendering or state mutation resulting from these workers must be securely dispatched back to the main thread loop using safe thread-proxies. File operations and persistent state must be guarded against race conditions with re-entrant locks.

### Pillar IV: Local-First Operations & Predictable Content Fidelity
*   **Principle**: Network requests should be surgical. Only actions explicitly requiring remote API communication (such as listing active tickets, researching solutions, posting comments, or uploading log attachments) should ever hit the network. All configuration auditing, bookmark resolution, catalog lookups, and cached search must run instantly on local files.
*   **Guideline**: Support rich, flexible local content pipelines:
    *   **File-Fed Content & Streaming**: Allow the TUI and CLI to consume case descriptions, comment bodies, and configuration parameters directly from local files. Large attachments must stream in small memory-bounded blocks to avoid memory exhaustion.
    *   **Portal Content Fidelity**: Translate natural operator inputs (like Markdown) into semantic, portal-compliant rich text (HTML) so comments render with proper line breaks and code formatting in the web portal, while roundtripping cleanly back to the terminal.
    *   **Dynamic Template Generation**: Leverage robust local templates (Jinja2) to dynamically compile case bodies and insert variables locally before transmitting any payload, minimizing API complexity and maximizing operator customization.
    *   **Simple Piped Fallback**: Maintain a global `--simple-output` mode that strips all ANSI coloring and layout grids to ensure perfect piping, logging, and remote SSH terminal compatibility.

### Pillar V: Extensive Test Coverage
*   **Principle**: All functionality is extensively tested; bug reports are reproduced first by adding a failing test before applying fixes.
*   **Guideline**: Maintain a complete, offline-capable test suite covering basic functionality and edge cases (such as token expiration recovery, date format variations, concurrency races, streaming chunk boundaries, and pagination limits).

---

## 4. Associated Documentation

* **[User Guide](user_guide.md)**: Practical task workflows, command-line references, templates, bookmarks, and TUI controls.
* **[Developer Guide](developer_guide.md)**: Technical architecture, GraphQL and REST API protocols, streaming engines, and mock test design.
