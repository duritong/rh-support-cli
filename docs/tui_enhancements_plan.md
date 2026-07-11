# TUI Enhancements and Architectural Overhaul Plan

This document outlines the phased implementation plan for overhauling the CLI/TUI architecture and introducing new interactive features to the TUI.

## Phase 1: Architectural Overhaul (The API Client)
Currently, authentication is handled procedurally: a token is retrieved at startup and passed as a string to commands and the TUI. This causes 401 Unauthorized errors in long-lived TUI sessions when the token expires.
**Actionable Steps:**
1.  **Introduce `RedHatAPIClient`**: Create a robust API client class in `rh_support_lib/api.py`. It will handle fetching the offline token, managing the cached OIDC token, and wrapping all `requests` calls.
2.  **Auto-Refresh Logic**: The client will intercept 401 responses, automatically force a token refresh, and retry the request transparently.
3.  **Refactor Dependencies**: Update `main.py`, all single-command scripts in `rh_support_lib/commands/`, and the `SupportApp` to accept and use `api_client` instead of raw token strings.

## Phase 2: Contextual Refresh Behaviors
Currently, the "Refresh" action globally refreshes the case list, even when triggered from the case view.
**Actionable Steps:**
1.  **Action Button Adjustment**: Wire the `🔄 Refresh (R)` button in the case details action bar to explicitly call `fetch_case_details(self.selected_case_id)` instead of refreshing the entire case list.
2.  **Contextual Keybinding**: Update the `r` keybinding logic to check focus. If `#case-list-container` is focused, refresh the list. If `#case-detail-container` is focused, refresh the case details.

## Phase 3: Inline Comment Pane with Status Integration
The current `CommentModal` blocks the screen, preventing users from reading the case history while drafting a response.
**Actionable Steps:**
1.  **Layout Redesign**: Replace `CommentModal` with an inline `CommentPane` mounted at the bottom of the right-hand column (underneath `#case-detail-container`). When activated, it will consume ~1/3 of the vertical height, leaving the case details scrollable above it.
2.  **Status Dropdown Integration**: Add a `Select` dropdown to the `CommentPane` for case status. 
    *   It will default to the case's current status.
    *   *Smart Default*: If the current status is "Waiting on Customer", the dropdown will automatically pre-select "Waiting on Red Hat".
    *   When posting the comment, if the status differs from the current case status, a secondary `PUT` request will be sent to update the case fields.
3.  **UI Polish & Controls**: Use small, compact buttons for "Post" and "Cancel". Bind the `Escape` key inside the pane to safely cancel/hide the pane. Implement an `action_focus_pane` (f) expansion for the comment pane so it can be full-screened.
4.  **Dirty State Management**: Track modifications inside the text area. If the user clicks a different case in the case list while the comment pane is open with unsaved text, intercept the selection and pop up an `UnsavedChangesModal` with options to "Discard", "Save to File", or "Cancel Navigation".

## Phase 4: File Attachments in TUI
Users need the ability to upload diagnostic files directly from the TUI.
**Actionable Steps:**
1.  **Action Button**: Add a `📎 Attach (A)` button to the case details action bar.
2.  **Attach Modal**: Implement an `AttachModal` containing a Textual `Input` field for the file path.
3.  **Upload Logic**: Validate file existence locally, then reuse the `cmd_attach` logic (via the new `api_client`) inside a background worker to upload the file and refresh the case details upon success.

---
*Each phase will be implemented, verified with unit tests, and reviewed before advancing to the next.*