import os
import requests
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Header,
    Footer,
    DataTable,
    Static,
    Button,
    Label,
    TextArea,
    Select,
)
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from rh_support_lib.constants import API_URL, STATUS_FILTER_MAP, SEVERITY_MAP
from rh_support_lib.api import get_json


def build_filter_payload(
    config: dict, bookmark: str = None, no_default_bookmark: bool = False
) -> dict:
    """Constructs the filter payload based on config bookmarks."""
    payload = {"maxResults": 50, "offset": 0}
    if not config:
        return payload

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

    # Convert filters to payload
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

        if any("closed" in s.lower() for s in mapped_statuses):
            payload["includeClosed"] = True

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

    return payload


class CommentModal(ModalScreen[str]):
    """Modal screen for posting comments."""

    def __init__(self, case_id: str):
        super().__init__()
        self.case_id = case_id

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(f"Post Comment to Case #{self.case_id}", id="modal-title"),
            TextArea(id="comment-body", show_line_numbers=True),
            Horizontal(
                Button("Post", variant="success", id="submit-btn"),
                Button("Cancel", variant="error", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="comment-modal-grid",
        )

    @on(Button.Pressed, "#submit-btn")
    def submit(self) -> None:
        text_area = self.query_one("#comment-body", TextArea)
        self.dismiss(text_area.text)

    @on(Button.Pressed, "#cancel-btn")
    def cancel(self) -> None:
        self.dismiss("")


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
            try:
                for f in os.listdir(templates_dir):
                    if f.endswith(".yaml") or f.endswith(".yml"):
                        choices.append((os.path.splitext(f)[0], os.path.splitext(f)[0]))
            except Exception:
                pass

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
            id="template-modal-grid",
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

    def __init__(self, config: dict, active_bookmark: str = None):
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


class FocusableContainer(VerticalScroll):
    """A scrollable container that can receive keyboard focus and handles keyboard scrolling."""

    can_focus = True
    BINDINGS = [
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
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("c", "add_comment", "Add Comment"),
        ("t", "apply_template", "Apply Template"),
        ("b", "select_bookmark", "Select Bookmark"),
        ("f", "focus_pane", "Focus Pane"),
        ("x", "exit_focus", "Exit Focus"),
    ]

    CSS = """
    * {
        scrollbar-size: 1 1;
    }
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 2fr 3fr;
        scrollbar-size: 1 1;
    }
    Screen.focused-left {
        layout: vertical;
    }
    Screen.focused-left #case-detail-container {
        display: none;
    }
    Screen.focused-left #case-list-container {
        width: 100%;
        height: 100%;
    }
    Screen.focused-right {
        layout: vertical;
    }
    Screen.focused-right #case-list-container {
        display: none;
    }
    Screen.focused-right #case-detail-container {
        width: 100%;
        height: 100%;
    }
    ScrollBar {
        background: transparent;
        color: $primary;
    }
    ScrollBar:hover {
        color: $primary-lighten-1;
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
        margin: 1;
        padding: 1;
        height: 100%;
    }
    #case-detail-container {
        border: solid yellow;
        margin: 1;
        padding: 1;
        height: 100%;
        overflow-y: scroll;
    }
    #case-detail-container:focus {
        border: double yellow;
        background: $surface;
    }
    #case-table {
        height: 100%;
        scrollbar-size: 1 1;
    }
    #case-table ScrollBar {
        scrollbar-size: 1 1;
        background: transparent;
    }
    CommentModal, TemplateModal {
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
    #tui-action-row {
        height: 1;
        margin-top: 1;
        margin-bottom: 1;
        align: center middle;
    }
    #tui-action-row Button {
        height: 1;
        min-width: 15;
        border: none;
        padding: 0 1;
        margin: 0 1;
    }
    """

    def __init__(
        self,
        token,
        config: dict,
        bookmark: str = None,
        no_default_bookmark: bool = False,
    ):
        super().__init__()
        from rh_support_lib.api import RedHatAPIClient, LegacyAPIClient

        if not isinstance(token, (RedHatAPIClient, LegacyAPIClient)):
            token = LegacyAPIClient(token)
        self.api_client = token
        self.config = config
        self.active_bookmark = bookmark
        self.no_default_bookmark = no_default_bookmark
        self.cases = []
        self.selected_case_id = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            DataTable(id="case-table"),
            id="case-list-container",
        )
        yield FocusableContainer(
            Static("Select a case to view details..."),
            id="case-detail-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#case-table", DataTable)
        table.add_columns("NUMBER", "TITLE", "SEVERITY", "STATUS")
        table.cursor_type = "row"
        self.run_worker(self.fetch_cases, thread=True)

    def fetch_cases(self) -> None:
        """Fetches the list of cases in the background."""

        def show_loading():
            container = self.query_one("#case-detail-container")
            container.query("*").remove()
            container.mount(Static("Loading cases..."))

        self.call_from_thread(show_loading)

        try:
            payload = build_filter_payload(
                self.config, self.active_bookmark, self.no_default_bookmark
            )
            token = self.api_client.get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            resp = requests.post(
                f"{API_URL}/cases/filter",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    self.cases = data
                elif isinstance(data, dict):
                    self.cases = (
                        data.get("cases") or data.get("items") or data.get("list") or []
                    )

                self.call_from_thread(self.populate_cases_table)
            else:
                self.call_from_thread(
                    self.show_error, f"Failed to load cases: HTTP {resp.status_code}"
                )
        except Exception as e:
            self.call_from_thread(self.show_error, f"Network Error: {e}")

    def populate_cases_table(self) -> None:
        table = self.query_one("#case-table", DataTable)
        table.clear()
        for idx, c in enumerate(self.cases):
            num = str(c.get("caseNumber") or c.get("id") or "")
            title = c.get("summary") or ""
            sev = c.get("severity") or ""
            status = c.get("status") or ""

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

            table.add_row(
                num,
                title,
                Text(sev, style=sev_style),
                Text(status, style=status_style),
                key=num,
            )

        container = self.query_one("#case-detail-container")
        container.query("*").remove()
        container.mount(Static("Select a case from the list."))

    def show_error(self, msg: str) -> None:
        container = self.query_one("#case-detail-container")
        container.query("*").remove()
        container.mount(Static(f"[bold red]Error:[/] {msg}"))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_case_id = str(event.row_key.value)
        self.run_worker(
            lambda: self.fetch_case_details(self.selected_case_id), thread=True
        )

    @on(Button.Pressed, "#tui-comment-btn")
    def on_comment_click(self) -> None:
        self.action_add_comment()

    @on(Button.Pressed, "#tui-template-btn")
    def on_template_click(self) -> None:
        self.action_apply_template()

    @on(Button.Pressed, "#tui-bookmark-btn")
    def on_bookmark_click(self) -> None:
        self.action_select_bookmark()

    @on(Button.Pressed, "#tui-refresh-btn")
    def on_refresh_click(self) -> None:
        if self.selected_case_id:
            self.run_worker(
                lambda: self.fetch_case_details(self.selected_case_id),
                thread=True,
            )
        else:
            self.run_worker(self.fetch_cases, thread=True)

    def fetch_case_details(self, case_id: str) -> None:
        """Fetches the details and comments for the selected case."""

        def show_loading():
            container = self.query_one("#case-detail-container")
            container.query("*").remove()
            container.mount(Static(f"Fetching details for Case #{case_id}..."))

        self.call_from_thread(show_loading)

        try:
            token = self.api_client.get_token()
            case = get_json(f"{API_URL}/cases/{case_id}", token)
            if not case or not isinstance(case, dict):
                self.call_from_thread(self.show_error, f"Case {case_id} not found.")
                return

            comments = get_json(f"{API_URL}/cases/{case_id}/comments", token)
            if not isinstance(comments, list):
                comments = []

            # Sort comments
            comments.sort(key=lambda x: x.get("createdDate", ""), reverse=False)

            self.call_from_thread(self.render_case_details, case, comments)
        except Exception as e:
            self.call_from_thread(self.show_error, f"Failed to fetch details: {e}")

    def render_case_details(self, case: dict, comments: list) -> None:
        detail_container = self.query_one("#case-detail-container")
        detail_container.query("*").remove()

        # Build beautiful layout
        num = case.get("caseNumber") or case.get("id") or ""
        title = case.get("summary") or ""
        product = case.get("product") or ""
        version = case.get("version") or ""
        status = case.get("status") or ""
        severity = case.get("severity") or ""
        case_type = case.get("caseType") or case.get("type") or ""
        account_num = case.get("accountNumber") or ""
        created_by = case.get("createdById") or "Unknown"
        created_date = case.get("createdDate") or ""
        description = case.get("description") or ""

        owner_obj = case.get("owner") or case.get("contact") or {}
        owner = ""
        if isinstance(owner_obj, dict):
            owner = owner_obj.get("name") or owner_obj.get("fullName") or ""

        # Construct Rich objects to print into details screen
        widgets = []

        # Header Action Buttons
        widgets.append(
            Horizontal(
                Button("💬 Comment (C)", id="tui-comment-btn", variant="success"),
                Button("📋 Template (T)", id="tui-template-btn", variant="primary"),
                Button("🔖 Bookmark (B)", id="tui-bookmark-btn", variant="warning"),
                Button("🔄 Refresh (R)", id="tui-refresh-btn", variant="default"),
                id="tui-action-row",
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

        # Description
        widgets.append(Static("\n[bold magenta]DESCRIPTION:[/]\n"))
        widgets.append(Static(Markdown(description)))

        # Comments Header
        widgets.append(Static(f"\n[bold cyan]COMMENTS ({len(comments)}):[/]\n"))

        # Comments panels
        for c in comments:
            c_date = c.get("createdDate", "")
            c_by = c.get("createdBy", "Unknown")

            is_public = "Public"
            border_style = "green"
            if c.get("isPublic") is False:
                vis = c.get("visibility")
                is_public = f"Private ({vis})" if vis else "Private"
                border_style = "red"

            c_body = c.get("commentBody") or c.get("body") or c.get("text") or ""
            comment_header = f"{c_by} ({is_public}) - {c_date}"
            widgets.append(
                Static(
                    Panel(
                        Markdown(c_body),
                        title=comment_header,
                        border_style=border_style,
                    )
                )
            )

        # Update TUI Viewport with compiled child widgets mounted directly into VerticalScroll
        detail_container.mount(*widgets)

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
            self.run_worker(self.fetch_cases, thread=True)
        else:
            if self.selected_case_id:
                self.run_worker(
                    lambda: self.fetch_case_details(self.selected_case_id),
                    thread=True,
                )

    def action_add_comment(self) -> None:
        if not self.selected_case_id:
            self.show_error("Please select a case first.")
            return

        def handle_comment(comment_body: str) -> None:
            if comment_body:
                self.run_worker(lambda: self.submit_comment(comment_body), thread=True)

        self.push_screen(CommentModal(self.selected_case_id), handle_comment)

    def submit_comment(self, body: str) -> None:
        def show_posting():
            container = self.query_one("#case-detail-container")
            container.query("*").remove()
            container.mount(Static("Posting comment..."))

        self.call_from_thread(show_posting)

        try:
            payload = {"isPublic": True, "commentBody": body}
            token = self.api_client.get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            resp = requests.post(
                f"{API_URL}/cases/{self.selected_case_id}/comments",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code in [200, 201]:
                self.fetch_case_details(self.selected_case_id)
            else:
                self.call_from_thread(
                    self.show_error, f"Failed to post comment: HTTP {resp.status_code}"
                )
        except Exception as e:
            self.call_from_thread(self.show_error, f"Error: {e}")

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
        def show_applying():
            container = self.query_one("#case-detail-container")
            container.query("*").remove()
            container.mount(Static("Applying template..."))

        self.call_from_thread(show_applying)

        try:
            # We construct a mock args class to invoke apply_template logic
            from types import SimpleNamespace
            from rh_support_lib.commands.apply_template import cmd_apply

            # We create a dummy args class
            args = SimpleNamespace(
                case=self.selected_case_id,
                template=[template_name],
                template_var=[],
                dry_run=False,
                simple_output=True,
            )
            # cmd_apply does a sys.exit on completion or errors.
            # To prevent TUI from crashing/exiting, we can wrap it or execute its logic safely
            # Let's catch SystemExit and handle refreshing case details afterwards!
            try:
                cmd_apply(args, self.api_client, self.config)
            except SystemExit as ex:
                if ex.code != 0:
                    self.call_from_thread(
                        self.show_error, "Template application failed."
                    )
                    return

            self.fetch_case_details(self.selected_case_id)
        except Exception as e:
            self.call_from_thread(self.show_error, f"Error: {e}")

    def action_select_bookmark(self) -> None:
        def handle_bookmark(bookmark_name: str) -> None:
            if bookmark_name:
                self.active_bookmark = bookmark_name
                self.run_worker(self.fetch_cases, thread=True)

        self.push_screen(
            BookmarkModal(self.config, self.active_bookmark), handle_bookmark
        )

    def action_focus_pane(self) -> None:
        """Zooms / focuses the currently highlighted pane into fullscreen."""
        focused_widget = self.focused
        if not focused_widget:
            return

        # Traverse up to find which parent container is focused
        current = focused_widget
        is_left = False
        while current:
            if current.id in ["case-table", "case-list-container"]:
                is_left = True
                break
            if current.id == "case-detail-container":
                is_left = False
                break
            current = current.parent

        if is_left:
            self.screen.remove_class("focused-right")
            self.screen.add_class("focused-left")
        else:
            self.screen.remove_class("focused-left")
            self.screen.add_class("focused-right")

    def action_exit_focus(self) -> None:
        """Restores the standard dual-pane split view layout."""
        self.screen.remove_class("focused-left")
        self.screen.remove_class("focused-right")


def cmd_tui(args, api_client, config):
    """Entry point to run the TUI."""
    bookmark = getattr(args, "bookmark", None)
    no_default = getattr(args, "no_default_bookmark", False)
    app = SupportApp(api_client, config, bookmark, no_default)
    app.run()
