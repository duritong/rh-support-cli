from __future__ import annotations

import contextlib
import os
import pathlib
import re
import threading
import time
from typing import ClassVar

from rich.box import ROUNDED
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)

from rh_support_lib.constants import (
    API_URL,
    SEVERITY_MAP,
    STATUS_FILTER_MAP,
)
from rh_support_lib.models import Attachment, Case, CaseDetails
from rh_support_lib.services.attachments import AttachmentService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.statuses import (
    determine_reply_status,
    get_status_choices_for_case,
)
from rh_support_lib.utils import is_comment_newer

__all__ = [
    "API_URL",
    "SupportApp",
    "build_filter_params",
    "build_filter_payload",
    "cmd_tui",
]


def build_filter_payload(
    config: dict, bookmark: str | None = None, no_default_bookmark: bool = False
) -> dict:
    """Constructs the filter payload based on config bookmarks."""
    if not config:
        return {}

    filters = {}

    selected_bk = None
    if bookmark == "none":
        selected_bk = None
    elif bookmark:
        selected_bk = bookmark
    elif not no_default_bookmark:
        selected_bk = config.get("default_bookmark")

    if selected_bk:
        bks = selected_bk if isinstance(selected_bk, list) else [selected_bk]
        for bk_name in bks:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)

    payload: dict = {}
    acc = filters.get("account") or filters.get("accountNumber")
    if acc:
        payload["accountNumber"] = str(acc)

    raw_status = filters.get("status")
    if raw_status:
        if isinstance(raw_status, str):
            raw_status = [raw_status]
        mapped_statuses = []
        for s in raw_status:
            val = STATUS_FILTER_MAP.get(str(s).lower(), s)
            for v in val.split(","):
                mapped_statuses.append(v.strip())
        if len(mapped_statuses) == 1:
            payload["status"] = mapped_statuses[0]
        else:
            payload["statuses"] = mapped_statuses

    raw_sev = filters.get("severity")
    if raw_sev:
        if isinstance(raw_sev, str):
            raw_sev = [raw_sev]
        mapped_sevs = []
        for s in raw_sev:
            mapped_sevs.append(SEVERITY_MAP.get(str(s).lower(), s))
        if len(mapped_sevs) == 1:
            payload["severity"] = mapped_sevs[0]
        else:
            payload["severities"] = mapped_sevs

    own = filters.get("owner") or filters.get("ownerSSOName")
    if own:
        payload["ownerSSOName"] = str(own)
    if filters.get("following"):
        payload["following"] = True

    return payload


def build_filter_params(
    config: dict, bookmark: str | None = None, no_default_bookmark: bool = False
) -> dict:
    """Constructs the filter parameters dictionary for CaseService."""
    payload = build_filter_payload(config, bookmark, no_default_bookmark)
    stat = payload.get("statuses") or payload.get("status")
    sev = payload.get("severities") or payload.get("severity")
    return {
        "account": payload.get("accountNumber"),
        "status": stat if isinstance(stat, list) else ([stat] if stat else None),
        "severity": sev if isinstance(sev, list) else ([sev] if sev else None),
        "owner": payload.get("ownerSSOName"),
        "following": bool(payload.get("following")),
    }


class TemplateModal(ModalScreen[str]):
    """Modal screen for applying templates."""

    def __init__(self, case_id: str, default_template: str = ""):
        super().__init__()
        self.case_id = case_id
        self.default_template = default_template

    def compose(self) -> ComposeResult:
        # Load local templates
        templates_dir = os.path.expanduser("~/.config/rh-support-cli/templates")
        choices = []
        if os.path.isdir(templates_dir):
            import contextlib

            with contextlib.suppress(OSError):
                for f in os.listdir(templates_dir):
                    if f.endswith((".yaml", ".yml")):
                        choices.append((os.path.splitext(f)[0], os.path.splitext(f)[0]))

        if not choices:
            choices = [("No templates found", "none")]

        initial_value = None
        for name, _ in choices:
            if name == self.default_template:
                initial_value = name
                break

        yield Grid(
            Label(f"Apply Template to Case #{self.case_id}", id="modal-title"),
            Select(
                choices,
                prompt="Select a template",
                value=initial_value,
                id="template-select",
            ),
            Horizontal(
                Button("Apply", variant="success", id="apply-btn"),
                Button("Cancel", variant="error", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="template-modal-grid",  # reuse template modal size!
        )

    @on(Button.Pressed, "#apply-btn")
    def apply(self) -> None:
        select = self.query_one("#template-select", Select)
        if select.value and select.value != "none":
            self.dismiss(select.value)
        else:
            self.dismiss("")

    @on(Button.Pressed, "#cancel-btn")
    def cancel(self) -> None:
        self.dismiss("")


class BookmarkModal(ModalScreen[str]):
    """Modal screen for selecting and applying a bookmark."""

    def __init__(self, config: dict, active_bookmark: str | None = None):
        super().__init__()
        self.config = config
        self.active_bookmark = active_bookmark

    def compose(self) -> ComposeResult:
        bks = self.config.get("bookmarks", {})
        choices = [("None (Clear Filters)", "none")]
        for name in sorted(bks.keys()):
            choices.append((name, name))

        initial_value = None
        for name, _ in choices:
            if name == self.active_bookmark:
                initial_value = name
                break

        yield Grid(
            Label("Select Active Filter Bookmark", id="modal-title"),
            Select(
                choices,
                prompt="Select a bookmark",
                value=initial_value,
                id="bookmark-select",
            ),
            Horizontal(
                Button("Select", variant="success", id="select-btn"),
                Button("Cancel", variant="error", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="template-modal-grid",  # reuse template modal size!
        )

    @on(Button.Pressed, "#select-btn")
    def apply_bk(self) -> None:
        select = self.query_one("#bookmark-select", Select)
        if select.value:
            self.dismiss(select.value)
        else:
            self.dismiss("")

    @on(Button.Pressed, "#cancel-btn")
    def cancel_bk(self) -> None:
        self.dismiss("")


class UnsavedChangesModal(ModalScreen[str]):
    """Modal screen shown when there are unsaved comment drafts."""

    def __init__(self, comment_text: str):
        super().__init__()
        self.comment_text = comment_text

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Unsaved Draft Comment", id="modal-title"),
            Label(
                "You have an unsaved comment draft. What would you like to do?",
                id="unsaved-label",
            ),
            Horizontal(
                Button("Discard", variant="error", id="discard-btn"),
                Button("Save to File", variant="primary", id="save-btn"),
                Button("Cancel", variant="default", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="template-modal-grid",  # reuse template modal size
        )

    @on(Button.Pressed, "#discard-btn")
    def discard(self) -> None:
        self.dismiss("discard")

    @on(Button.Pressed, "#save-btn")
    def save(self) -> None:
        self.dismiss("save")

    @on(Button.Pressed, "#cancel-btn")
    def cancel(self) -> None:
        self.dismiss("cancel")


class AttachModal(ModalScreen[str]):
    """Modal screen for specifying a file to attach to a case."""

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Attach File to Case", id="modal-title"),
            Label("Specify the local path of the file you want to upload:"),
            Input(placeholder="/path/to/file.txt", id="attach-file-input"),
            Horizontal(
                Button("Attach", variant="success", id="attach-btn"),
                Button("Cancel", variant="error", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="template-modal-grid",  # reuse template modal size!
        )

    @on(Button.Pressed, "#attach-btn")
    def attach(self) -> None:
        inp = self.query_one("#attach-file-input", Input)
        val = inp.value.strip()
        if val:
            self.dismiss(val)
        else:
            self.dismiss("")

    @on(Button.Pressed, "#cancel-btn")
    def cancel_attach(self) -> None:
        self.dismiss("")


class DownloadModal(ModalScreen[tuple[str, str] | None]):
    """Modal screen for selecting an attachment and destination to download."""

    def __init__(self, attachments: list[Attachment]):
        super().__init__()
        self.attachments = attachments

    def compose(self) -> ComposeResult:
        options = []
        for a in self.attachments:
            size_str = (
                f"{a.size_kb:.1f} KB"
                if a.size_kb < 1024
                else f"{a.size_kb / 1024.0:.1f} MB"
            )
            options.append((f"{a.file_name} ({size_str})", a.uuid))

        default_val = options[0][1] if options else Select.BLANK
        yield Grid(
            Label("Download Attachment", id="modal-title"),
            Label("Select Attachment:"),
            Select(
                options,
                value=default_val,
                id="download-select",
                prompt="Choose an attachment",
            ),
            Label("Destination Path / Directory:"),
            Input(
                value=".",
                placeholder="./filename or destination dir",
                id="download-path-input",
            ),
            Horizontal(
                Button("Download", variant="success", id="download-btn"),
                Button("Cancel", variant="error", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="template-modal-grid",
        )

    @on(Button.Pressed, "#download-btn")
    def do_download(self) -> None:
        sel = self.query_one("#download-select", Select)
        inp = self.query_one("#download-path-input", Input)
        att_uuid = str(sel.value) if sel.value and sel.value != Select.BLANK else ""
        dest = inp.value.strip() or "."
        if att_uuid:
            self.dismiss((att_uuid, dest))
        else:
            self.dismiss(None)

    @on(Button.Pressed, "#cancel-btn")
    def cancel_download(self) -> None:
        self.dismiss(None)


class FocusableContainer(VerticalScroll):
    """A scrollable container that can receive keyboard focus and handles keyboard scrolling."""

    can_focus = True
    BINDINGS: ClassVar[list] = [
        Binding("up", "scroll_up", "Scroll Up"),
        Binding("down", "scroll_down", "Scroll Down"),
        Binding("pageup", "scroll_page_up", "Scroll Page Up", show=False),
        Binding("pagedown", "scroll_page_down", "Scroll Page Down", show=False),
        Binding("home", "scroll_home", "Scroll Home", show=False),
        Binding("end", "scroll_end", "Scroll End", show=False),
    ]

    def action_scroll_up(self) -> None:
        self.scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self.scroll_down(animate=False)

    def action_scroll_page_up(self) -> None:
        self.scroll_page_up(animate=False)

    def action_scroll_page_down(self) -> None:
        self.scroll_page_down(animate=False)

    def action_scroll_home(self) -> None:
        self.scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.scroll_end(animate=False)


class SupportApp(App):
    """Terminal User Interface (TUI) for Red Hat Support Case CLI."""

    TITLE = "Red Hat Support CLI"
    BINDINGS: ClassVar[list] = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("c", "add_comment", "Add Comment"),
        ("a", "add_attachment", "Attach File"),
        ("d", "download_attachment", "Download"),
        ("t", "apply_template", "Apply Template"),
        ("w", "toggle_follow", "Follow"),
        ("m", "mark_read", "Mark Read"),
        ("v", "toggle_layout", "Split (H/V)"),
        Binding("l", "toggle_layout", "Toggle Layout", show=False),
        ("f", "focus_pane", "Focus Pane"),
        Binding("ctrl+up", "focus_top", "Focus Top", priority=True),
        Binding("ctrl+down", "focus_bottom", "Focus Bottom", priority=True),
        Binding("ctrl+left", "focus_left", "Focus Left", priority=True),
        Binding("ctrl+right", "focus_right", "Focus Right", priority=True),
        Binding("escape", "escape_action", "Cancel/Unfocus", show=False),
        Binding("ctrl+x", "exit_commenting", "Cancel Commenting", show=False),
    ]

    CSS = """
    Screen {
        layout: grid;
        grid-size: 1 2;
        grid-rows: 2fr 3fr;
        grid-columns: 1fr;
        scrollbar-size: 1 1;
    }
    Screen.commenting {
        grid-rows: 1fr 2fr;
    }
    Screen.layout-vertical {
        layout: grid;
        grid-size: 2;
        grid-columns: 2fr 3fr;
        grid-rows: 1fr;
        scrollbar-size: 1 1;
    }
    Screen.layout-vertical.commenting {
        grid-rows: 1fr;
    }
    Screen.focused-left, Screen.layout-vertical.focused-left {
        layout: vertical;
    }
    Screen.focused-left #right-column-container, Screen.layout-vertical.focused-left #right-column-container {
        display: none;
    }
    Screen.focused-left #case-list-container, Screen.layout-vertical.focused-left #case-list-container {
        width: 100%;
        height: 100%;
    }
    Screen.focused-right, Screen.layout-vertical.focused-right {
        layout: vertical;
    }
    Screen.focused-right #case-list-container, Screen.layout-vertical.focused-right #case-list-container {
        display: none;
    }
    Screen.focused-right #right-column-container, Screen.layout-vertical.focused-right #right-column-container {
        width: 100%;
        height: 100%;
    }
    Screen.focused-comment, Screen.layout-vertical.focused-comment {
        layout: vertical;
    }
    Screen.focused-comment #case-list-container, Screen.layout-vertical.focused-comment #case-list-container {
        display: none;
    }
    Screen.focused-comment #case-detail-container, Screen.layout-vertical.focused-comment #case-detail-container {
        display: none;
    }
    Screen.focused-comment #comment-pane-container, Screen.layout-vertical.focused-comment #comment-pane-container {
        width: 100%;
        height: 100%;
        display: block;
    }
    Header {
        background: $primary-darken-1;
        color: $text;
        dock: top;
    }
    Footer {
        dock: bottom;
    }
    #case-list-container {
        border: solid cyan;
        margin: 0;
        padding: 0;
        height: 100%;
    }
    #case-list-container:focus-within {
        border: double cyan;
    }
    #right-column-container {
        height: 100%;
        layout: vertical;
        margin: 0;
        padding: 0;
    }
    #case-detail-container {
        border: solid yellow;
        margin: 0;
        padding: 0 1;
        height: 100%;
        overflow-y: scroll;
    }
    #case-detail-container:focus {
        border: double yellow;
        background: $surface;
    }
    Screen.commenting #case-detail-container {
        height: 65%;
    }
    Screen.commenting #comment-pane-container {
        display: block;
    }
    #comment-pane-container {
        border: solid $primary;
        height: 35%;
        margin: 0;
        padding: 0 1;
        background: $surface;
        display: none;
    }
    #comment-pane-container:focus-within {
        border: double $primary;
    }
    #tui-comment-pane-title {
        margin-top: 1;
    }
    #comment-pane-header-row {
        height: 3;
        margin-bottom: 1;
        align: left middle;
    }
    #comment-pane-header-row Label {
        margin-right: 1;
        margin-top: 1;
    }
    #comment-pane-header-row Select {
        width: 30;
        height: 3;
    }
    #tui-comment-textarea {
        height: 1fr;
        min-height: 4;
        border: solid $primary;
    }
    #comment-pane-buttons-row {
        height: 1;
        margin-top: 1;
        align: right middle;
    }
    #case-table {
        height: 100%;
        scrollbar-size: 1 1;
        overflow-y: scroll;
    }
    #case-table ScrollBar {
        scrollbar-size: 1 1;
        background: transparent;
    }
    TemplateModal, UnsavedChangesModal, BookmarkModal, AttachModal {
        align: center middle;
    }
    #comment-modal-grid {
        padding: 1 2;
        width: 80;
        height: 26;
        border: thick $primary 80%;
        background: $surface;
    }
    #template-modal-grid {
        padding: 1 2;
        width: 65;
        height: 18;
        border: thick $primary 80%;
        background: $surface;
    }
    #unsaved-label {
        text-align: center;
        margin-bottom: 1;
    }
    #modal-title {
        text-align: center;
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    #comment-body {
        height: 14;
        border: solid $primary;
    }
    #modal-buttons {
        align: center middle;
        margin-top: 1;
        height: 3;
    }
    #modal-buttons Button {
        margin: 0 1;
    }
    .tui-action-row {
        height: 1;
        margin-top: 0;
        margin-bottom: 1;
        align: center middle;
    }
    .tui-action-row Button, #comment-pane-container Button {
        height: 1;
        min-width: 15;
        border: none;
        padding: 0 1;
        margin: 0 1;
    }
    ScrollBar {
        background: transparent;
    }
    """

    def __init__(
        self,
        token: str,
        config: dict | None = None,
        bookmark: str | None = None,
        no_default_bookmark: bool = False,
        layout: str | None = None,
    ):
        super().__init__()
        from rh_support_lib.api import LegacyAPIClient, RedHatAPIClient

        if not isinstance(token, (RedHatAPIClient, LegacyAPIClient)):
            token = LegacyAPIClient(token)
        self.api_client = token
        self.config = config
        self.active_bookmark = bookmark
        self.no_default_bookmark = no_default_bookmark
        self.cases: list[Case] = []
        self.selected_case_id: str | None = None
        self.current_details: CaseDetails | None = None
        self.current_attachments: list[Attachment] = []
        self.active_jobs: list[str] = []
        self.case_service = CaseService(self.api_client)
        self.attachment_service = AttachmentService(self.api_client)
        cache_cfg = (config.get("cache") or {}) if config else {}
        tui_cfg = (config.get("tui") or {}) if config else {}
        try:
            self.poll_interval = int(
                tui_cfg.get("poll_interval")
                if "poll_interval" in tui_cfg
                else cache_cfg.get("tui_poll_interval", 60)
            )
        except (ValueError, TypeError):
            self.poll_interval = 60
        # Resolve initial layout: explicit CLI arg > config > state.yaml > default ('vertical')
        user = ""
        with contextlib.suppress(Exception):
            user = self.case_service.get_current_user()
        saved_layout = "vertical"
        with contextlib.suppress(Exception):
            saved_layout = self.case_service.state_service.get_tui_layout(user)

        initial_layout = (
            layout
            or (config.get("tui_layout") if config else None)
            or (config.get("layout") if config else None)
            or saved_layout
            or "horizontal"
        ).lower()
        self.layout = (
            "vertical" if initial_layout in ("vertical", "vsplit") else "horizontal"
        )

    def _safe_call(self, fn, *args, **kwargs) -> None:
        """Invokes a UI callback directly if on main thread, or via call_from_thread if in worker."""
        if threading.current_thread() is threading.main_thread():
            fn(*args, **kwargs)
        else:
            self.call_from_thread(fn, *args, **kwargs)

    @contextlib.contextmanager
    def track_job(self, description: str):
        self._safe_call(self._start_job, description)
        try:
            yield
        finally:
            self._safe_call(self._finish_job, description)

    def _start_job(self, description: str) -> None:
        if description not in self.active_jobs:
            self.active_jobs.append(description)
        self._sync_header_status()

    def _finish_job(self, description: str) -> None:
        if description in self.active_jobs:
            self.active_jobs.remove(description)
        self._sync_header_status()

    def _sync_header_status(self) -> None:
        if self.active_jobs:
            jobs_str = " | ".join(self.active_jobs)
            self.sub_title = f"⟳ {jobs_str}"
        else:
            self.sub_title = ""

    def format_title(self, title: str, sub_title: str) -> Content:
        title_content = Content(title)
        if sub_title:
            return Content.assemble(
                title_content,
                (" — ", "dim"),
                Content(sub_title).stylize("bold yellow"),
            )
        return title_content

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            DataTable(id="case-table"),
            id="case-list-container",
        )
        yield Vertical(
            FocusableContainer(
                Static("Select a case to view details..."),
                id="case-detail-container",
            ),
            Container(id="comment-pane-container"),
            id="right-column-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#case-table", DataTable)
        table.add_columns("NUMBER", "TITLE", "SEVERITY", "STATUS")
        table.cursor_type = "row"
        if self.layout == "vertical":
            self.screen.add_class("layout-vertical")
        if self.poll_interval > 0:
            self.set_interval(self.poll_interval, self.trigger_background_poll)
        self.run_worker(self.fetch_cases, thread=True)

    def fetch_cases(self, force: bool = False) -> None:
        """Fetches the list of cases in the background."""
        with self.track_job("Fetching cases..."):

            def show_loading():
                with contextlib.suppress(Exception):
                    container = self.query_one("#case-detail-container")
                    container.query("*").remove()
                    container.mount(Static("Loading cases..."))

            if not self.selected_case_id:
                self._safe_call(show_loading)
            try:
                params = build_filter_params(
                    self.config, self.active_bookmark, self.no_default_bookmark
                )
                if params.get("following"):
                    self.cases = self.case_service.list_followed_cases(
                        account=params.get("account")
                    )
                else:
                    list_kwargs = {k: v for k, v in params.items() if k != "following"}
                    if force:
                        list_kwargs["use_cache"] = False
                    self.cases = self.case_service.list_cases(**list_kwargs)
                self._safe_call(self.populate_cases_table)
            except Exception as e:  # noqa: BLE001
                self._safe_call(self.show_error, f"Network Error: {e}")

    def populate_cases_table(self) -> None:
        table = self.query_one("#case-table", DataTable)
        table.clear()
        for idx, c in enumerate(self.cases):
            num = c.case_number or c.id
            title = c.summary or ""
            sev = c.severity or ""
            status = c.status or ""

            # Standard color representations
            sev_style = ""
            if "Urgent" in sev or "1" in sev:
                sev_style = "bold red"
            elif "High" in sev or "2" in sev:
                sev_style = "red"
            elif "Normal" in sev or "3" in sev:
                sev_style = "yellow"
            elif "Low" in sev or "4" in sev:
                sev_style = "green"
            status_style = ""
            if "Red Hat" in status or "red hat" in status.lower():
                status_style = "bold orange3"
            elif "customer" in status.lower():
                status_style = "bold green"
            elif "closed" in status.lower():
                status_style = "dim"

            num_text = Text(num, style=status_style)
            if getattr(c, "is_followed", False):
                num_text.append(" ★", style="bold yellow")
            if getattr(c, "has_unread", False):
                cnt = getattr(c, "unread_count", 0)
                num_text.append(f" ●{cnt}" if cnt else " ●", style="bold cyan")

            table.add_row(
                num_text,
                title,
                Text(sev, style=sev_style),
                Text(status, style=status_style),
                key=num,
            )

        if not self.selected_case_id:
            container = self.query_one("#case-detail-container")
            container.query("*").remove()
            container.mount(Static("Select a case from the list."))

    def trigger_background_poll(self) -> None:
        """Periodic background poll for live case updates without disturbing active UI focus."""
        self.run_worker(self.poll_cases_background, thread=True)

    def poll_cases_background(self) -> None:
        """Polls for updated cases and unread badges, and refreshes current details if modified."""
        with (
            self.track_job("Syncing updates..."),
            contextlib.suppress(Exception),
        ):
            params = build_filter_params(
                self.config, self.active_bookmark, self.no_default_bookmark
            )
            if params.get("following"):
                fresh_cases = self.case_service.list_followed_cases(
                    account=params.get("account")
                )
            else:
                list_kwargs = {k: v for k, v in params.items() if k != "following"}
                list_kwargs["use_cache"] = False
                fresh_cases = self.case_service.list_cases(**list_kwargs)

            if not fresh_cases:
                return

            self.cases = fresh_cases
            self.call_from_thread(self.update_table_rows_preserve_cursor)

            if self.selected_case_id:
                matching = next(
                    (
                        c
                        for c in fresh_cases
                        if str(c.case_number) == str(self.selected_case_id)
                        or str(c.id) == str(self.selected_case_id)
                    ),
                    None,
                )
                known_lmd = matching.last_modified_date if matching else None
                cached = self.case_service.cache_service.get_case_details(
                    self.selected_case_id
                )
                cached_lmd = cached[2] if cached else None

                if known_lmd and cached_lmd and known_lmd != cached_lmd:
                    details = self.case_service.get_case_details(
                        self.selected_case_id, use_cache=False
                    )
                    try:
                        atts = self.attachment_service.list_attachments(
                            self.selected_case_id
                        )
                    except Exception:  # noqa: BLE001
                        atts = []
                    if details:
                        self.call_from_thread(self.render_case_details, details, atts)

    def update_table_rows_preserve_cursor(self) -> None:
        """Refreshes the DataTable with self.cases while preserving cursor row position."""
        table = self.query_one("#case-table", DataTable)
        current_row_key = None
        with contextlib.suppress(Exception):
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
            if cell_key:
                current_row_key = str(cell_key.row_key.value)
        table.clear()
        for idx, c in enumerate(self.cases):
            num = c.case_number or c.id
            title = c.summary or ""
            sev = c.severity or ""
            status = c.status or ""

            sev_style = ""
            if "Urgent" in sev or "1" in sev:
                sev_style = "bold red"
            elif "High" in sev or "2" in sev:
                sev_style = "red"
            elif "Normal" in sev or "3" in sev:
                sev_style = "yellow"
            elif "Low" in sev or "4" in sev:
                sev_style = "green"
            status_style = ""
            if "Red Hat" in status or "red hat" in status.lower():
                status_style = "bold orange3"
            elif "customer" in status.lower():
                status_style = "bold green"
            elif "closed" in status.lower():
                status_style = "dim"

            num_text = Text(num, style=status_style)
            if getattr(c, "is_followed", False):
                num_text.append(" ★", style="bold yellow")
            if getattr(c, "has_unread", False):
                cnt = getattr(c, "unread_count", 0)
                num_text.append(f" ●{cnt}" if cnt else " ●", style="bold cyan")

            table.add_row(
                num_text,
                title,
                Text(sev, style=sev_style),
                Text(status, style=status_style),
                key=num,
            )

        if current_row_key:
            with contextlib.suppress(Exception):
                for row_idx, r_key in enumerate(table.rows):
                    if str(r_key.value) == current_row_key:
                        table.move_cursor(row=row_idx)
                        break

    def update_case_table_row(self, case_num: str) -> None:
        with contextlib.suppress(Exception):
            table = self.query_one("#case-table", DataTable)
            for c in self.cases:
                c_num_str = str(c.case_number or c.id or "")
                if c_num_str == str(case_num) or c_num_str.lstrip("0") == str(
                    case_num
                ).lstrip("0"):
                    status = c.status or ""
                    status_style = ""
                    if "Red Hat" in status or "red hat" in status.lower():
                        status_style = "bold orange3"
                    elif "customer" in status.lower():
                        status_style = "bold green"
                    elif "closed" in status.lower():
                        status_style = "dim"
                    num_text = Text(c_num_str, style=status_style)
                    if getattr(c, "is_followed", False):
                        num_text.append(" ★", style="bold yellow")
                    if getattr(c, "has_unread", False):
                        cnt = getattr(c, "unread_count", 0)
                        num_text.append(f" ●{cnt}" if cnt else " ●", style="bold cyan")
                    cols = list(table.columns.keys())
                    if cols:
                        target_key = None
                        for r_k in table.rows:
                            r_k_val = str(r_k.value)
                            if r_k_val == c_num_str or r_k_val.lstrip(
                                "0"
                            ) == c_num_str.lstrip("0"):
                                target_key = r_k
                                break
                        if target_key:
                            table.update_cell(target_key, cols[0], num_text)
                        else:
                            table.update_cell(str(case_num), cols[0], num_text)
                    break

    def show_error(self, msg: str) -> None:
        container = self.query_one("#case-detail-container")
        container.query("*").remove()
        clean_msg = re.sub(r"^(?:Error:\s*)+", "", str(msg).strip())
        container.mount(Static(f"[bold red]Error:[/] {clean_msg}"))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        next_case_id = str(event.row_key.value)
        if self.selected_case_id == next_case_id:
            return

        # Check if commenting is open and holds dirty text
        if self.screen.has_class("commenting"):
            text_area = self.query_one("#tui-comment-textarea", TextArea)
            comment_text = text_area.text.strip()
            if comment_text:
                self.prompt_unsaved_changes_on_navigation(comment_text, next_case_id)
                return
            else:
                self.screen.remove_class("commenting")
                text_area.text = ""

        # SWR: If details exist in local disk cache, render immediately without waiting
        self.selected_case_id = next_case_id
        cached_rec = self.case_service.cache_service.get_case_details(
            next_case_id, ttl=0
        )
        if cached_rec:
            cached_details, cached_atts, _ = cached_rec
            self.render_case_details(cached_details, cached_atts)

        # Proceed to validate/fetch fresh case details in background
        self.run_worker(
            lambda: self.fetch_case_details(self.selected_case_id), thread=True
        )

    @on(Button.Pressed, ".tui-comment-btn, #tui-comment-btn")
    def on_comment_click(self) -> None:
        self.action_add_comment()

    @on(Button.Pressed, ".tui-attach-btn, #tui-attach-btn")
    def on_attach_click(self) -> None:
        self.action_add_attachment()

    @on(Button.Pressed, ".tui-template-btn, #tui-template-btn")
    def on_template_click(self) -> None:
        self.action_apply_template()

    @on(Button.Pressed, ".tui-bookmark-btn, #tui-bookmark-btn")
    def on_bookmark_click(self) -> None:
        self.action_select_bookmark()

    @on(Button.Pressed, ".tui-refresh-btn, #tui-refresh-btn")
    def on_refresh_click(self) -> None:
        if self.selected_case_id:
            self.run_worker(
                lambda: self.fetch_case_details(self.selected_case_id, force=True),
                thread=True,
            )
        else:
            self.run_worker(lambda: self.fetch_cases(force=True), thread=True)

    @on(Button.Pressed, ".tui-download-btn, #tui-download-btn")
    def on_download_click(self) -> None:
        self.action_download_attachment()

    @on(Button.Pressed, "#tui-comment-post-btn")
    def on_comment_post_click(self) -> None:
        text_area = self.query_one("#tui-comment-textarea", TextArea)
        comment_body = text_area.text.strip()
        if not comment_body:
            self.show_error("Comment body cannot be empty.")
            return

        select = self.query_one("#tui-comment-status-select", Select)
        target_status = select.value

        self.run_worker(
            lambda: self.execute_comment_submission(comment_body, target_status),
            thread=True,
        )

    @on(Button.Pressed, "#tui-comment-save-btn")
    def on_comment_save_click(self) -> None:
        text_area = self.query_one("#tui-comment-textarea", TextArea)
        comment_text = text_area.text.strip()
        if not comment_text:
            self.show_error("Comment draft is empty.")
            return
        self.save_comment_to_file(comment_text)

    @on(Button.Pressed, "#tui-comment-cancel-btn")
    def on_comment_cancel_click(self) -> None:
        self.action_exit_commenting()

    def fetch_case_details(self, case_id: str, force: bool = False) -> None:
        """Fetches the details and comments for the selected case."""
        with self.track_job(f"Loading #{case_id}..."):
            is_already_rendered = (
                self.selected_case_id == case_id
                and self.current_details is not None
                and (
                    str(self.current_details.case.case_number) == str(case_id)
                    or str(self.current_details.case.id) == str(case_id)
                )
            )

            def show_loading():
                with contextlib.suppress(Exception):
                    container = self.query_one("#case-detail-container")
                    container.query("*").remove()
                    container.mount(Static(f"Fetching details for Case #{case_id}..."))

            if not is_already_rendered:
                self._safe_call(show_loading)

            try:
                details = self.case_service.get_case_details(
                    case_id,
                    use_cache=not force,
                )
                if not details or not details.case:
                    self._safe_call(self.show_error, f"Case {case_id} not found.")
                    return

                try:
                    attachments = self.attachment_service.list_attachments(case_id)
                except Exception:  # noqa: BLE001
                    attachments = []

                self._safe_call(self.render_case_details, details, attachments)
            except Exception as e:  # noqa: BLE001
                self._safe_call(self.show_error, f"Failed to fetch details: {e}")

    def render_case_details(
        self, details: CaseDetails, attachments: list[Attachment]
    ) -> None:
        self.current_details = details
        self.current_attachments = attachments
        detail_container = self.query_one("#case-detail-container")
        detail_container.query("*").remove()
        case = details.case
        num = case.case_number or case.id or ""
        title = case.summary or ""
        product = case.product or ""
        version = case.version or ""
        status = case.status or ""
        severity = case.severity or ""
        case_type = case.case_type or ""
        account_num = case.account_number or ""
        created_by = case.created_by or "Unknown"
        created_date = case.created_date or ""
        description = details.description or ""
        owner = case.owner or "Unknown"
        comments = details.comments

        user = self.case_service.get_current_user()
        tc = self.case_service.state_service.get_tracked_cases(user).get(str(num))

        status_changed = False
        old_status = ""
        last_read_date = ""
        last_read_id = ""
        if tc and tc.read_checkpoint:
            old_status = tc.read_checkpoint.status
            if old_status and status and old_status != status:
                status_changed = True
            last_read_date = (
                tc.read_checkpoint.last_read_comment_date
                or tc.read_checkpoint.marked_read_at
                or ""
            )
            last_read_id = tc.read_checkpoint.last_read_comment_id or ""

        unread_indices = set()
        if tc and tc.read_checkpoint:
            for idx, c in enumerate(comments):
                if is_comment_newer(
                    created_date=c.created_date,
                    comment_id=c.id,
                    checkpoint_date=last_read_date,
                    checkpoint_id=last_read_id,
                ):
                    unread_indices.add(idx)
        widgets = []

        # Header Action Buttons
        widgets.append(
            Horizontal(
                Button("💬 Comment (C)", classes="tui-comment-btn", variant="success"),
                Button("📎 Attach (A)", classes="tui-attach-btn", variant="primary"),
                Button(
                    "📥 Download (D)", classes="tui-download-btn", variant="primary"
                ),
                Button(
                    "📋 Template (T)", classes="tui-template-btn", variant="primary"
                ),
                Button(
                    "🔖 Bookmark (B)", classes="tui-bookmark-btn", variant="warning"
                ),
                Button("🔄 Refresh (R)", classes="tui-refresh-btn", variant="default"),
                classes="tui-action-row",
            )
        )

        # Header panel
        header_text = Text()
        header_text.append(f"CASE: {num}\n", style="bold cyan")
        header_text.append(f"TITLE: {title}\n", style="bold white")
        header_text.append(
            f"URL: https://access.redhat.com/support/cases/{num}",
            style="dim underline",
        )
        widgets.append(Static(Panel(header_text, border_style="cyan")))

        # Metadata grid
        meta_table = Table.grid(padding=(0, 2))
        meta_table.add_column(style="bold yellow", width=14)
        meta_table.add_column(style="white")
        meta_table.add_column(style="bold yellow", width=14)
        meta_table.add_column(style="white")

        meta_table.add_row("Product:", f"{product} {version}", "Status:", status)
        meta_table.add_row("Severity:", severity, "Type:", case_type)
        meta_table.add_row(
            "Account:",
            str(account_num),
            "Created By:",
            f"{created_by} on {created_date}",
        )
        meta_table.add_row("Assignee:", owner, "", "")
        widgets.append(
            Static(Panel(meta_table, title="Details", border_style="yellow"))
        )
        if status_changed:
            diff_banner = f"[bold yellow]⚡ Status Changed:[/] {old_status} ➔ [bold green]{status}[/]"
            widgets.append(Static(Panel(diff_banner, border_style="yellow")))
        # Attachments Section
        if attachments:
            widgets.append(
                Static(f"\n[bold yellow]ATTACHMENTS ({len(attachments)}):[/]\n")
            )
            att_table = Table(box=ROUNDED, header_style="bold yellow", show_lines=False)
            att_table.add_column("Filename", style="bold white")
            att_table.add_column("Size", justify="right", style="green")
            att_table.add_column("Uploaded By", style="yellow")
            att_table.add_column("Status", style="cyan")
            for att in attachments:
                size_str = (
                    f"{att.size_kb:.1f} KB"
                    if att.size_kb < 1024
                    else f"{att.size_kb / 1024.0:.1f} MB"
                )
                att_table.add_row(
                    att.file_name,
                    size_str,
                    att.created_by or "Unknown",
                    att.upload_status,
                )
            widgets.append(Static(att_table))

        # Description
        widgets.append(Static("\n[bold magenta]DESCRIPTION:[/]\n"))
        widgets.append(Static(Markdown(description)))

        # Comments Header
        widgets.append(Static(f"\n[bold cyan]COMMENTS ({len(comments)}):[/]\n"))

        checkpoint_shown = False
        for idx, c in enumerate(comments):
            is_unread = idx in unread_indices
            if is_unread and not checkpoint_shown:
                checkpoint_shown = True
                count_unread = len(unread_indices)
                ckpt_time = (
                    tc.read_checkpoint.marked_read_at or last_read_date or "previous"
                )
                divider = Panel(
                    f"[bold yellow]┈┈┈┈ 📍 Last read checkpoint ({ckpt_time}) │ ▼ {count_unread} unread below ┈┈┈┈[/]",
                    border_style="yellow",
                )
                widgets.append(Static(divider, classes="tui-checkpoint-divider"))

            c_date = c.created_date or ""
            c_by = c.created_by or "Unknown"
            is_public = (
                "Public"
                if c.is_public
                else (
                    f"Private ({c.visibility})"
                    if c.visibility and c.visibility != "Private"
                    else "Private"
                )
            )
            border_style = (
                "yellow" if is_unread else ("green" if c.is_public else "red")
            )
            badge = " [bold yellow][NEW][/]" if is_unread else ""
            c_body = c.body or ""
            comment_header = f"{c_by} ({is_public}){badge} - {c_date}"
            widgets.append(
                Static(
                    Panel(
                        Markdown(c_body),
                        title=comment_header,
                        border_style=border_style,
                    )
                )
            )
        detail_container.mount(*widgets)

        if checkpoint_shown:

            def scroll_to_divider() -> None:
                with contextlib.suppress(Exception):
                    div_widget = self.query_one(".tui-checkpoint-divider")
                    detail_container.scroll_to_widget(div_widget, top=True)

            self.call_after_refresh(scroll_to_divider)

        if tc and (status_changed or unread_indices):
            self.set_timer(
                2.5,
                lambda: self._auto_mark_read_dwell(str(num)),
                name=f"dwell_read_{num}",
            )

    def _auto_mark_read_dwell(self, case_num: str) -> None:
        if str(self.selected_case_id) != str(case_num) and str(
            self.selected_case_id
        ).lstrip("0") != str(case_num).lstrip("0"):
            return
        user = self.case_service.get_current_user()
        status = None
        severity = None
        last_comm_id = None
        last_comm_date = None
        if self.current_details:
            c_num = str(
                self.current_details.case.case_number
                or self.current_details.case.id
                or ""
            )
            if c_num == str(case_num) or c_num.lstrip("0") == str(case_num).lstrip("0"):
                status = self.current_details.case.status
                severity = self.current_details.case.severity
                if self.current_details.comments:
                    last_comm = self.current_details.comments[-1]
                    last_comm_id = last_comm.id
                    last_comm_date = last_comm.created_date

        self.case_service.state_service.mark_case_read(
            user=user,
            case_number=str(case_num),
            status=status,
            severity=severity,
            last_comment_id=last_comm_id,
            last_comment_date=last_comm_date,
        )
        for c in self.cases:
            c_num = str(c.case_number or c.id or "")
            if c_num == str(case_num) or c_num.lstrip("0") == str(case_num).lstrip("0"):
                c.has_unread = False
                c.unread_count = 0
                c.status_changed = False
                break
        self.update_case_table_row(str(case_num))

    def action_mark_read(self) -> None:
        case_id = self.selected_case_id
        if not case_id:
            with contextlib.suppress(Exception):
                table = self.query_one("#case-table", DataTable)
                if (
                    table
                    and table.cursor_row is not None
                    and table.cursor_row < len(table.rows)
                ):
                    row_keys = list(table.rows.keys())
                    case_id = str(row_keys[table.cursor_row].value)
        if not case_id:
            return

        user = self.case_service.get_current_user()
        status = None
        severity = None
        last_comm_id = None
        last_comm_date = None
        if self.current_details:
            c_num = str(
                self.current_details.case.case_number
                or self.current_details.case.id
                or ""
            )
            if c_num == str(case_id) or c_num.lstrip("0") == str(case_id).lstrip("0"):
                status = self.current_details.case.status
                severity = self.current_details.case.severity
                if self.current_details.comments:
                    last_comm = self.current_details.comments[-1]
                    last_comm_id = last_comm.id
                    last_comm_date = last_comm.created_date

        self.case_service.state_service.mark_case_read(
            user=user,
            case_number=case_id,
            status=status,
            severity=severity,
            last_comment_id=last_comm_id,
            last_comment_date=last_comm_date,
        )

        for c in self.cases:
            c_num = str(c.case_number or c.id or "")
            if c_num == str(case_id) or c_num.lstrip("0") == str(case_id).lstrip("0"):
                c.has_unread = False
                c.unread_count = 0
                c.status_changed = False
                break

        self.update_case_table_row(case_id)

        if self.selected_case_id and (
            str(self.selected_case_id) == str(case_id)
            or str(self.selected_case_id).lstrip("0") == str(case_id).lstrip("0")
        ):
            self.run_worker(
                lambda: self.fetch_case_details(self.selected_case_id), thread=True
            )

    def action_toggle_follow(self) -> None:
        case_id = self.selected_case_id
        if not case_id:
            with contextlib.suppress(Exception):
                table = self.query_one("#case-table", DataTable)
                if (
                    table
                    and table.cursor_row is not None
                    and table.cursor_row < len(table.rows)
                ):
                    row_keys = list(table.rows.keys())
                    case_id = str(row_keys[table.cursor_row].value)
        if not case_id:
            return
        user = self.case_service.get_current_user()
        is_followed = self.case_service.state_service.toggle_follow(user, case_id)
        for c in self.cases:
            c_num = str(c.case_number or c.id or "")
            if c_num == str(case_id) or c_num.lstrip("0") == str(case_id).lstrip("0"):
                c.is_followed = is_followed
                break
        self.update_case_table_row(case_id)

    def action_toggle_layout(self) -> None:
        """Toggle between horizontal split (mailbox style) and vertical split (side-by-side)."""
        if "layout-vertical" in self.screen.classes:
            self.screen.remove_class("layout-vertical")
            self.layout = "horizontal"
        else:
            self.screen.add_class("layout-vertical")
            self.layout = "vertical"
        with contextlib.suppress(Exception):
            user = self.case_service.get_current_user()
            self.case_service.state_service.set_tui_layout(user, self.layout)

    def action_refresh(self) -> None:
        """Contextual refresh depending on which pane is focused."""
        focused_widget = self.focused
        is_left = True
        if focused_widget:
            current = focused_widget
            while current:
                if current.id in ["case-table", "case-list-container"]:
                    is_left = True
                    break
                if current.id == "case-detail-container":
                    is_left = False
                    break
                current = current.parent

        if is_left:
            self.run_worker(lambda: self.fetch_cases(force=True), thread=True)
        else:
            if self.selected_case_id:
                self.run_worker(
                    lambda: self.fetch_case_details(self.selected_case_id, force=True),
                    thread=True,
                )

    def action_add_comment(self) -> None:
        if not self.selected_case_id:
            self.show_error("Please select a case first.")
            return

        # Show the inline comment pane
        self.screen.add_class("commenting")
        self.compose_comment_pane()

        # Focus on the newly mounted TextArea after the refresh cycle
        def focus_textarea() -> None:
            with contextlib.suppress(Exception):
                self.query_one("#tui-comment-textarea", TextArea).focus()

        self.call_after_refresh(focus_textarea)

    def compose_comment_pane(self) -> None:
        container = self.query_one("#comment-pane-container")
        if len(container.children) > 0:
            self.update_comment_status_dropdown()
            return

        case = (
            next(
                (
                    c
                    for c in self.cases
                    if str(c.case_number or c.id) == self.selected_case_id
                ),
                None,
            )
            if self.selected_case_id
            else None
        )
        current_status = case.status if case else None
        status_choices = get_status_choices_for_case(current_status)
        target_status = determine_reply_status(current_status)
        container.mount(
            Vertical(
                Label("[bold cyan]Drafting Comment...[/]", id="tui-comment-pane-title"),
                Horizontal(
                    Label("Apply Status: "),
                    Select(
                        status_choices,
                        id="tui-comment-status-select",
                        prompt="Select Status",
                        value=target_status,
                    ),
                    id="comment-pane-header-row",
                ),
                TextArea(id="tui-comment-textarea", show_line_numbers=True),
                Horizontal(
                    Button("💬 Post", variant="success", id="tui-comment-post-btn"),
                    Button(
                        "💾 Save Draft", variant="default", id="tui-comment-save-btn"
                    ),
                    Button("❌ Cancel", variant="error", id="tui-comment-cancel-btn"),
                    id="comment-pane-buttons-row",
                ),
            )
        )

    def update_comment_status_dropdown(self) -> None:
        case = (
            next(
                (
                    c
                    for c in self.cases
                    if (c.case_number or c.id) == self.selected_case_id
                ),
                None,
            )
            if self.selected_case_id
            else None
        )
        current_status = case.status if case else None
        target_status = determine_reply_status(current_status)

        with contextlib.suppress(Exception):
            select = self.query_one("#tui-comment-status-select", Select)
            valid_values = [val for _, val in select._options]
            if target_status not in valid_values:
                select.set_options(get_status_choices_for_case(target_status))
            select.value = target_status

    def execute_comment_submission(self, comment_body: str, target_status: str) -> None:
        with self.track_job(f"Posting comment on #{self.selected_case_id}..."):

            def show_posting():
                self.screen.remove_class("commenting")
                with contextlib.suppress(Exception):
                    self.query_one("#tui-comment-textarea", TextArea).text = ""
                with contextlib.suppress(Exception):
                    container = self.query_one("#case-detail-container")
                    container.query("*").remove()
                    container.mount(Static("Posting comment..."))

            self._safe_call(show_posting)
            try:
                self.case_service.add_comment(
                    self.selected_case_id,
                    comment_body,
                    is_public=True,
                    status=target_status if target_status else None,
                )

                self.fetch_case_details(self.selected_case_id, force=True)
                self.fetch_cases(force=True)

            except Exception as e:  # noqa: BLE001
                self._safe_call(self.show_error, str(e))

    def action_exit_commenting(self) -> None:
        """Hides the comment pane, checking for unsaved changes if needed."""
        if not self.screen.has_class("commenting"):
            return

        text_area = self.query_one("#tui-comment-textarea", TextArea)
        comment_text = text_area.text.strip()

        if comment_text:
            self.prompt_unsaved_changes(comment_text)
        else:
            self.screen.remove_class("commenting")
            text_area.text = ""

    def prompt_unsaved_changes(self, comment_text: str) -> None:
        def handle_choice(choice: str) -> None:
            if choice == "discard":
                self.screen.remove_class("commenting")
                self.query_one("#tui-comment-textarea", TextArea).text = ""
            elif choice == "save":
                self.save_comment_to_file(comment_text)

        self.push_screen(UnsavedChangesModal(comment_text), handle_choice)

    def prompt_unsaved_changes_on_navigation(
        self, comment_text: str, next_case_id: str
    ) -> None:
        def handle_choice(choice: str) -> None:
            if choice == "discard":
                self.screen.remove_class("commenting")
                self.query_one("#tui-comment-textarea", TextArea).text = ""
                self.selected_case_id = next_case_id
                self.run_worker(
                    lambda: self.fetch_case_details(self.selected_case_id),
                    thread=True,
                )
            elif choice == "save":
                self.save_comment_to_file(comment_text, next_case_id)

        self.push_screen(UnsavedChangesModal(comment_text), handle_choice)

    def save_comment_to_file(
        self, comment_text: str, next_case_id: str | None = None
    ) -> None:
        drafts_dir = os.path.expanduser("~/.config/rh-support-cli/drafts")
        try:
            pathlib.Path(drafts_dir).mkdir(parents=True, exist_ok=True)
            timestamp = int(time.time())
            filename = f"comment_draft_{self.selected_case_id}_{timestamp}.txt"
            path = os.path.join(drafts_dir, filename)
            with open(path, "w") as f:
                f.write(comment_text)

            # Inform user of file location
            def show_saved():
                container = self.query_one("#case-detail-container")
                container.query("*").remove()
                container.mount(
                    Static(f"[bold green]Success:[/] Draft saved to {path}")
                )

            self.call_from_thread(show_saved)

            self.screen.remove_class("commenting")
            self.query_one("#tui-comment-textarea", TextArea).text = ""

            if next_case_id:
                time.sleep(1)
                self.selected_case_id = next_case_id
                self.run_worker(
                    lambda: self.fetch_case_details(self.selected_case_id),
                    thread=True,
                )
        except OSError as e:
            self.show_error(f"Failed to save draft to file: {e}")

    def action_add_attachment(self) -> None:
        if not self.selected_case_id:
            self.show_error("Please select a case first.")
            return

        def handle_file(file_path: str) -> None:
            if file_path:
                if not os.path.isfile(file_path):
                    self.show_error(f"Local file '{file_path}' does not exist.")
                    return
                self.run_worker(
                    lambda: self.execute_attachment_upload(file_path),
                    thread=True,
                )

        self.push_screen(AttachModal(), handle_file)

    def execute_attachment_upload(self, file_path: str) -> None:
        with self.track_job(f"Uploading '{os.path.basename(file_path)}'..."):

            def show_uploading():
                container = self.query_one("#case-detail-container")
                container.query("*").remove()
                container.mount(
                    Static(f"Uploading attachment '{os.path.basename(file_path)}'...")
                )

            self.call_from_thread(show_uploading)

            try:
                self.attachment_service.upload_attachment(
                    self.selected_case_id, file_path
                )
                self.fetch_case_details(self.selected_case_id, force=True)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.show_error, f"Error uploading attachment: {e}"
                )

    def action_apply_template(self) -> None:
        if not self.selected_case_id:
            self.show_error("Please select a case first.")
            return

        def handle_template(template_name: str) -> None:
            if template_name:
                self.run_worker(
                    lambda: self.execute_template(template_name), thread=True
                )

        default_tmpl = self.config.get("default_create_template") or ""
        self.push_screen(
            TemplateModal(self.selected_case_id, default_tmpl), handle_template
        )

    def execute_template(self, template_name: str) -> None:
        with self.track_job(f"Applying template '{template_name}'..."):

            def show_applying():
                container = self.query_one("#case-detail-container")
                container.query("*").remove()
                container.mount(Static("Applying template..."))

            self.call_from_thread(show_applying)

            try:
                from types import SimpleNamespace

                from rh_support_lib.commands.apply_template import cmd_apply

                args = SimpleNamespace(
                    case=self.selected_case_id,
                    template=[template_name],
                    template_var=[],
                    dry_run=False,
                    simple_output=True,
                )
                try:
                    cmd_apply(args, self.api_client, self.config)
                except SystemExit as ex:
                    if ex.code != 0:
                        self.call_from_thread(
                            self.show_error, "Template application failed."
                        )
                        return

                self.fetch_case_details(self.selected_case_id, force=True)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(self.show_error, f"Error: {e}")

    def action_download_attachment(self) -> None:
        if not self.selected_case_id:
            self.show_error("Please select a case first.")
            return

        if not getattr(self, "current_attachments", None):
            self.notify(
                "No attachments found on this case.",
                title="Download Attachment",
                severity="warning",
            )
            return

        def handle_download(result: tuple[str, str] | None) -> None:
            if not result:
                return
            att_uuid, dest_path = result
            self.run_worker(
                lambda: self.execute_download(
                    self.selected_case_id, att_uuid, dest_path
                ),
                thread=True,
            )

        self.push_screen(DownloadModal(self.current_attachments), handle_download)

    def execute_download(self, case_id: str, att_uuid: str, dest_path: str) -> None:
        with self.track_job(f"Downloading '{att_uuid}'..."):

            def show_downloading():
                with contextlib.suppress(Exception):
                    self.notify(
                        f"Downloading attachment '{att_uuid}'...",
                        title="Download Starting",
                        severity="information",
                    )

            self.call_from_thread(show_downloading)
            try:
                saved_path = self.attachment_service.download_attachment(
                    case_id, att_uuid, target_path=dest_path
                )

                def show_success():
                    self.notify(
                        f"Downloaded attachment to: {saved_path}",
                        title="Download Complete",
                        severity="information",
                    )

                self.call_from_thread(show_success)
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(
                    self.show_error, f"Error downloading attachment: {e}"
                )

    def action_select_bookmark(self) -> None:
        def handle_bookmark(bookmark_name: str) -> None:
            if bookmark_name:
                self.active_bookmark = bookmark_name
                self.run_worker(self.fetch_cases, thread=True)

        self.push_screen(
            BookmarkModal(self.config, self.active_bookmark), handle_bookmark
        )

    def action_focus_pane(self) -> None:
        """Zooms / focuses the currently highlighted pane into fullscreen, or toggles it off."""
        # If we are already focused on ANY pane, pressing 'f' toggles it OFF!
        if (
            self.screen.has_class("focused-left")
            or self.screen.has_class("focused-right")
            or self.screen.has_class("focused-comment")
        ):
            self.action_exit_focus()
            return

        focused_widget = self.focused
        if not focused_widget:
            return

        # Traverse up to find which parent container is focused
        current = focused_widget
        is_left = False
        is_comment = False
        while current:
            if current.id in ["case-table", "case-list-container"]:
                is_left = True
                break
            if current.id == "comment-pane-container":
                is_comment = True
                break
            if current.id == "case-detail-container":
                is_left = False
                break
            current = current.parent

        if is_left:
            self.screen.remove_class("focused-right")
            self.screen.remove_class("focused-comment")
            self.screen.add_class("focused-left")
        elif is_comment:
            self.screen.remove_class("focused-left")
            self.screen.remove_class("focused-right")
            self.screen.add_class("focused-comment")
        else:
            self.screen.remove_class("focused-left")
            self.screen.remove_class("focused-comment")
            self.screen.add_class("focused-right")

    def action_exit_focus(self) -> None:
        """Restores the standard dual-pane split view layout."""
        self.screen.remove_class("focused-left")
        self.screen.remove_class("focused-right")
        self.screen.remove_class("focused-comment")

    def focus_list_pane(self) -> None:
        """Focus the case list table (top pane in horizontal, left pane in vertical)."""
        with contextlib.suppress(Exception):
            table = self.query_one("#case-table", DataTable)
            table.focus()
            if self.screen.has_class("focused-right") or self.screen.has_class(
                "focused-comment"
            ):
                self.screen.remove_class("focused-right")
                self.screen.remove_class("focused-comment")
                self.screen.add_class("focused-left")

    def focus_details_pane(self) -> None:
        """Focus the details pane (bottom pane in horizontal, right pane in vertical)."""
        with contextlib.suppress(Exception):
            if self.screen.has_class("commenting"):
                with contextlib.suppress(Exception):
                    self.query_one("#tui-comment-textarea", TextArea).focus()
                    if self.screen.has_class("focused-left") or self.screen.has_class(
                        "focused-right"
                    ):
                        self.screen.remove_class("focused-left")
                        self.screen.remove_class("focused-right")
                        self.screen.add_class("focused-comment")
                    return
            detail_container = self.query_one(
                "#case-detail-container", FocusableContainer
            )
            detail_container.focus()
            if self.screen.has_class("focused-left") or self.screen.has_class(
                "focused-comment"
            ):
                self.screen.remove_class("focused-left")
                self.screen.remove_class("focused-comment")
                self.screen.add_class("focused-right")

    def action_focus_top(self) -> None:
        self.focus_list_pane()

    def action_focus_bottom(self) -> None:
        self.focus_details_pane()

    def action_focus_left(self) -> None:
        self.focus_list_pane()

    def action_focus_right(self) -> None:
        self.focus_details_pane()

    def action_escape_action(self) -> None:
        """Handles Escape key: either exits fullscreen focus mode or exits commenting mode."""
        # 1. If we are currently in fullscreen focus mode, exit focus first!
        if (
            self.screen.has_class("focused-left")
            or self.screen.has_class("focused-right")
            or self.screen.has_class("focused-comment")
        ):
            self.action_exit_focus()
            return

        # 2. Otherwise, if we are in commenting mode, exit commenting!
        if self.screen.has_class("commenting"):
            self.action_exit_commenting()


def cmd_tui(args, api_client, config):
    """Entry point to run the TUI."""
    bookmark = getattr(args, "bookmark", None)
    no_default = getattr(args, "no_default_bookmark", False)
    layout = getattr(args, "layout", None)
    app = SupportApp(api_client, config, bookmark, no_default, layout=layout)
    app.run()
