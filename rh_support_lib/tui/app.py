import os
import requests
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Grid, Container, Vertical, Horizontal
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

from rh_support_lib.constants import API_URL
from rh_support_lib.api import get_json


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
            id="modal-grid",
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

    def __init__(self, case_id: str):
        super().__init__()
        self.case_id = case_id

    def compose(self) -> ComposeResult:
        # Load local templates
        templates_dir = os.path.expanduser("~/.config/rh-support-cli/templates")
        choices = []
        if os.path.isdir(templates_dir):
            try:
                for f in os.listdir(templates_dir):
                    if f.endswith(".yaml") or f.endswith(".yml"):
                        choices.append((os.path.splitext(f)[0], f))
            except Exception:
                pass

        if not choices:
            choices = [("No templates found", "none")]

        yield Grid(
            Label(f"Apply Template to Case #{self.case_id}", id="modal-title"),
            Select(choices, prompt="Select a template", id="template-select"),
            Horizontal(
                Button("Apply", variant="success", id="apply-btn"),
                Button("Cancel", variant="error", id="cancel-btn"),
                id="modal-buttons",
            ),
            id="modal-grid",
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


class SupportApp(App):
    """Terminal User Interface (TUI) for Red Hat Support Case CLI."""

    TITLE = "Red Hat Support CLI"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_cases", "Refresh"),
        ("c", "add_comment", "Add Comment"),
        ("t", "apply_template", "Apply Template"),
    ]

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 2fr 3fr;
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
    #case-table {
        height: 100%;
    }
    CommentModal, TemplateModal {
        align: center middle;
    }
    #modal-grid {
        padding: 1 2;
        width: 65;
        height: 18;
        border: thick $primary 80%;
        background: $surface;
    }
    #modal-title {
        text-align: center;
        font-weight: bold;
        color: $text;
        margin-bottom: 1;
    }
    #comment-body {
        height: 8;
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
    """

    def __init__(self, token: str, config: dict):
        super().__init__()
        self.token = token
        self.config = config
        self.cases = []
        self.selected_case_id = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            DataTable(id="case-table"),
            id="case-list-container",
        )
        yield Container(
            Static("Select a case to view details...", id="case-details"),
            id="case-detail-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#case-table", DataTable)
        table.add_columns("NUMBER", "TITLE", "SEVERITY", "STATUS")
        table.cursor_type = "row"
        self.run_worker(self.fetch_cases)

    def fetch_cases(self) -> None:
        """Fetches the list of cases in the background."""
        self.query_one("#case-details", Static).update("Loading cases...")
        try:
            payload = {"maxResults": 50, "offset": 0}
            headers = {
                "Authorization": f"Bearer {self.token}",
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

        self.query_one("#case-details", Static).update("Select a case from the list.")

    def show_error(self, msg: str) -> None:
        self.query_one("#case-details", Static).update(f"[bold red]Error:[/] {msg}")

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_case_id = str(event.row_key.value)
        self.run_worker(self.fetch_case_details, self.selected_case_id)

    def fetch_case_details(self, case_id: str) -> None:
        """Fetches the details and comments for the selected case."""
        details_view = self.query_one("#case-details", Static)
        details_view.update(f"Fetching details for Case #{case_id}...")

        try:
            case = get_json(f"{API_URL}/cases/{case_id}", self.token)
            if not case or not isinstance(case, dict):
                self.call_from_thread(self.show_error, f"Case {case_id} not found.")
                return

            comments = get_json(f"{API_URL}/cases/{case_id}/comments", self.token)
            if not isinstance(comments, list):
                comments = []

            # Sort comments
            comments.sort(key=lambda x: x.get("createdDate", ""), reverse=False)

            self.call_from_thread(self.render_case_details, case, comments)
        except Exception as e:
            self.call_from_thread(self.show_error, f"Failed to fetch details: {e}")

    def render_case_details(self, case: dict, comments: list) -> None:
        details_view = self.query_one("#case-details", Static)

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
        container = Vertical()

        # Header panel
        header_text = Text()
        header_text.append(f"CASE: {num}\n", style="bold cyan")
        header_text.append(f"TITLE: {title}\n", style="bold white")
        header_text.append(
            f"URL: https://access.redhat.com/support/cases/{num}",
            style="dim underline",
        )
        container.mount(Static(Panel(header_text, border_style="cyan")))

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
        container.mount(
            Static(Panel(meta_table, title="Details", border_style="yellow"))
        )

        # Description
        container.mount(Static("\n[bold magenta]DESCRIPTION:[/]\n"))
        container.mount(Static(Markdown(description)))

        # Comments Header
        container.mount(Static(f"\n[bold cyan]COMMENTS ({len(comments)}):[/]\n"))

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
            container.mount(
                Static(
                    Panel(
                        Markdown(c_body),
                        title=comment_header,
                        border_style=border_style,
                    )
                )
            )

        # Update TUI Viewport
        details_view.update(container)

    def action_refresh_cases(self) -> None:
        self.run_worker(self.fetch_cases)

    def action_add_comment(self) -> None:
        if not self.selected_case_id:
            self.show_error("Please select a case first.")
            return

        def handle_comment(comment_body: str) -> None:
            if comment_body:
                self.run_worker(self.submit_comment, comment_body)

        self.push_screen(CommentModal(self.selected_case_id), handle_comment)

    def submit_comment(self, body: str) -> None:
        self.query_one("#case-details", Static).update("Posting comment...")
        try:
            payload = {"isPublic": True, "commentBody": body}
            headers = {
                "Authorization": f"Bearer {self.token}",
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
                self.run_worker(self.execute_template, template_name)

        self.push_screen(TemplateModal(self.selected_case_id), handle_template)

    def execute_template(self, template_name: str) -> None:
        self.query_one("#case-details", Static).update("Applying template...")
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
                cmd_apply(args, self.token, self.config)
            except SystemExit as ex:
                if ex.code != 0:
                    self.call_from_thread(
                        self.show_error, "Template application failed."
                    )
                    return

            self.fetch_case_details(self.selected_case_id)
        except Exception as e:
            self.call_from_thread(self.show_error, f"Error: {e}")


def cmd_tui(args, token, config):
    """Entry point to run the TUI."""
    app = SupportApp(token, config)
    app.run()
