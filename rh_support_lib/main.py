import argparse
import os
import sys

from rh_support_lib.api import enable_debug_logging
from rh_support_lib.commands.actions import (
    cmd_attach,
    cmd_close,
    cmd_comment,
    cmd_completion,
    cmd_download,
    cmd_link,
)
from rh_support_lib.commands.apply_template import cmd_apply, cmd_list_templates
from rh_support_lib.commands.attachments import cmd_attachment
from rh_support_lib.commands.create_case import cmd_create
from rh_support_lib.commands.list_cases import cmd_list
from rh_support_lib.commands.search import cmd_search
from rh_support_lib.commands.show_case import cmd_show
from rh_support_lib.commands.tracking import cmd_sync, cmd_updates
from rh_support_lib.commands.watchers import (
    cmd_follow,
    cmd_following,
    cmd_unfollow,
)
from rh_support_lib.config import load_config
from rh_support_lib.tui.app import cmd_tui

try:
    import argcomplete
except ImportError:
    argcomplete = None


def main():
    if "--mock" in sys.argv:
        import http.server
        import threading

        import rh_support_lib.constants

        # Check if a custom mock-corpus is provided
        corpus_dir = None
        if "--mock-corpus" in sys.argv:
            try:
                idx = sys.argv.index("--mock-corpus")
                if idx + 1 < len(sys.argv):
                    corpus_dir = sys.argv[idx + 1]
            except ValueError:
                pass

        # Start loopback mock server
        from rh_support_lib.synthetic_corpus.server import StatefulMockHandler

        StatefulMockHandler.corpus_dir = corpus_dir

        server = http.server.HTTPServer(("127.0.0.1", 0), StatefulMockHandler)
        port = server.server_port

        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        mock_api_url = f"http://127.0.0.1:{port}"
        mock_sso_url = f"http://127.0.0.1:{port}/auth/token"
        mock_graphql_url = f"http://127.0.0.1:{port}/graphql"
        mock_attachment_url = f"http://127.0.0.1:{port}"

        os.environ["RH_API_URL"] = mock_api_url
        os.environ["RH_SSO_URL"] = mock_sso_url
        os.environ["RH_GRAPHQL_URL"] = mock_graphql_url
        os.environ["RH_ATTACHMENT_API_URL"] = mock_attachment_url
        os.environ.setdefault("REDHAT_SUPPORT_OFFLINE_TOKEN", "cli_token")
        os.environ.setdefault("REDHAT_SUPPORT_USERNAME", "jdeveloper")

        # Update bound variables in any loaded modules
        rh_support_lib.constants.API_URL = mock_api_url
        rh_support_lib.constants.SSO_URL = mock_sso_url
        rh_support_lib.constants.GRAPHQL_URL = mock_graphql_url
        rh_support_lib.constants.ATTACHMENT_API_URL = mock_attachment_url
        for mod_name, mod in list(sys.modules.items()):
            if mod_name.startswith("rh_support_lib.") or mod_name == "rh_support_lib":
                if hasattr(mod, "API_URL"):
                    mod.API_URL = mock_api_url
                if hasattr(mod, "SSO_URL"):
                    mod.SSO_URL = mock_sso_url
                if hasattr(mod, "GRAPHQL_URL"):
                    mod.GRAPHQL_URL = mock_graphql_url
                if hasattr(mod, "ATTACHMENT_API_URL"):
                    mod.ATTACHMENT_API_URL = mock_attachment_url
    parser = argparse.ArgumentParser(
        description="Red Hat Support Case CLI Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Authentication:
  The tool requires a Red Hat Offline Token. It looks for it in this order:
  1. CLI Argument: --token-file <path>
  2. Environment Variable: REDHAT_SUPPORT_OFFLINE_TOKEN
  3. Config File: ~/.config/rh-support-cli/token
  4. Interactive prompt
""",
    )
    parser.add_argument(
        "--token-file", help="Path to file containing the offline token"
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Output structured results in JSON format",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "yaml"],
        default=None,
        help="Output format: 'text' (default), 'json', or 'yaml'",
    )
    parser.add_argument(
        "--simple-output",
        action="store_true",
        help="Disable colored output and visual indicators",
    )
    parser.add_argument("--config-file", help="Path to configuration file")
    parser.add_argument(
        "--graphql",
        action="store_true",
        help="Use Red Hat GraphQL & v3 Attachment APIs (default)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass and do not update local disk cache",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--debug-file",
        help="Log debug output to file (implies --debug, disables truncation)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run the CLI/TUI completely offline against an in-process stateful mock support portal",
    )
    parser.add_argument(
        "--mock-corpus",
        help="Path to custom synthetic support corpus directory to use with --mock (defaults to a transient temporary directory)",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Subcommand to run"
    )

    # List Subcommand
    parser_list = subparsers.add_parser("list", help="List support cases")
    parser_list.add_argument("--account", help="Account Number")
    parser_list.add_argument(
        "--status",
        action="append",
        help="Filter by status. Maps 'customer' -> 'Waiting on Customer', 'redhat' -> 'Waiting on Red Hat', 'closed' -> 'Closed', 'open' -> 'Waiting on Red Hat & Customer'. (can be used multiple times)",
    )
    parser_list.add_argument(
        "--severity",
        action="append",
        help="Filter by severity. Values: Low, Normal, High, Urgent. (can be used multiple times)",
    )
    parser_list.add_argument("--owner", help="Filter by owner")
    parser_list.add_argument(
        "--following",
        "--watched",
        action="store_true",
        help="Filter to cases followed/watched by myself (or specified user)",
    )
    parser_list.add_argument(
        "--sync",
        action="store_true",
        help="Sync followed cases from account before listing",
    )
    parser_list.add_argument(
        "-u",
        "--user",
        help="Filter by specific SSO user when viewing followed cases (defaults to myself)",
    )
    parser_list.add_argument(
        "-b",
        "--bookmark",
        action="append",
        help="Use a filter bookmark from config (can be used multiple times)",
    )
    parser_list.add_argument(
        "--no-default-bookmark",
        action="store_true",
        help="Ignore default bookmark from config",
    )
    parser_list.add_argument(
        "--all",
        action="store_true",
        help="Fetch all matching cases across all pages",
    )
    parser_list.add_argument(
        "--limit",
        type=int,
        help="Maximum number of cases to fetch (default: 100)",
    )

    # Show Subcommand
    parser_show = subparsers.add_parser("show", help="Show case details")
    parser_show.add_argument("-c", "--case", required=True, help="Case Number")
    parser_show.add_argument(
        "--no-pager", action="store_true", help="Disable pager output"
    )
    parser_show.add_argument(
        "--mark-read", action="store_true", help="Mark this case's updates as read"
    )
    parser_show.add_argument(
        "--order",
        choices=["newest", "oldest", "desc", "asc"],
        default=None,
        help="Comment display order: 'newest' (default, matches portal) or 'oldest'",
    )
    parser_show.add_argument(
        "--newest-first",
        "--reverse",
        action="store_true",
        dest="newest_first",
        help="Display comments in reverse chronological order (newest first, default)",
    )
    parser_show.add_argument(
        "--oldest-first",
        action="store_true",
        dest="oldest_first",
        help="Display comments in chronological order (oldest first)",
    )
    parser_show.add_argument(
        "--simple-output", action="store_true", help="Disable colored output"
    )

    # Link Subcommand
    parser_link = subparsers.add_parser("link", help="Get the browser URL for a case")
    parser_link.add_argument("-c", "--case", required=True, help="Case Number")

    # Completion Subcommand
    subparsers.add_parser("completion", help="Print bash completion script")

    # Attach Subcommand (shortcut)
    parser_attach = subparsers.add_parser(
        "attach", help="Attach a file to a case (shortcut)"
    )
    parser_attach.add_argument("-c", "--case", required=True, help="Case Number")
    parser_attach.add_argument(
        "-f",
        "--file",
        required=True,
        action="append",
        help="File path to attach (can be used multiple times)",
    )
    parser_attach.add_argument("-d", "--description", help="Attachment description")

    # Download Subcommand (shortcut)
    parser_download = subparsers.add_parser(
        "download", help="Download an attachment from a case (shortcut)"
    )
    parser_download.add_argument("-c", "--case", required=True, help="Case Number")
    parser_download.add_argument(
        "-a", "--attachment", required=True, help="Attachment UUID or filename"
    )
    parser_download.add_argument("-o", "--output", help="Destination path or filename")
    # Attachment Subcommand Group
    for att_name in ("attachment", "attachments"):
        parser_att = subparsers.add_parser(
            att_name,
            help="Manage case file attachments (list, upload, download, delete, status)",
        )
        parser_att.add_argument("-c", "--case", help="Case Number")
        att_sub = parser_att.add_subparsers(
            dest="attachment_action", help="Attachment action"
        )

        # list
        p_att_list = att_sub.add_parser("list", help="List attachments for a case")
        p_att_list.add_argument(
            "-c", "--case", default=argparse.SUPPRESS, help="Case Number"
        )
        p_att_list.add_argument(
            "case_pos",
            nargs="?",
            default=argparse.SUPPRESS,
            help="Case Number (positional)",
        )
        p_att_list.add_argument(
            "--include-deleted", action="store_true", help="Include deleted files"
        )

        # upload / add
        for up_name in ("upload", "add"):
            p_att_up = att_sub.add_parser(
                up_name, help="Upload attachment(s) to a case"
            )
            p_att_up.add_argument(
                "-c", "--case", default=argparse.SUPPRESS, help="Case Number"
            )
            p_att_up.add_argument(
                "case_pos",
                nargs="?",
                default=argparse.SUPPRESS,
                help="Case Number (positional)",
            )
            p_att_up.add_argument(
                "-f", "--file", required=True, action="append", help="File to attach"
            )
            p_att_up.add_argument("-d", "--description", help="Attachment description")

        # download / get
        for down_name in ("download", "get"):
            p_att_down = att_sub.add_parser(
                down_name, help="Download an attachment from a case"
            )
            p_att_down.add_argument(
                "-c", "--case", default=argparse.SUPPRESS, help="Case Number"
            )
            p_att_down.add_argument(
                "case_pos",
                nargs="?",
                default=argparse.SUPPRESS,
                help="Case Number (positional)",
            )
            p_att_down.add_argument(
                "-a", "--attachment", required=True, help="Attachment UUID"
            )
            p_att_down.add_argument(
                "-o", "--output", help="Destination path or filename"
            )

        # delete / rm
        for del_name in ("delete", "rm"):
            p_att_del = att_sub.add_parser(
                del_name, help="Delete an attachment from a case"
            )
            p_att_del.add_argument(
                "-c", "--case", default=argparse.SUPPRESS, help="Case Number"
            )
            p_att_del.add_argument(
                "case_pos",
                nargs="?",
                default=argparse.SUPPRESS,
                help="Case Number (positional)",
            )
            p_att_del.add_argument(
                "-a", "--attachment", required=True, help="Attachment UUID"
            )
            p_att_del.add_argument(
                "-y", "--yes", action="store_true", help="Skip confirmation prompt"
            )

        # status
        p_att_stat = att_sub.add_parser(
            "status", help="Get upload status of case attachments"
        )
        p_att_stat.add_argument(
            "-c", "--case", default=argparse.SUPPRESS, help="Case Number"
        )
        p_att_stat.add_argument(
            "case_pos",
            nargs="?",
            default=argparse.SUPPRESS,
            help="Case Number (positional)",
        )
        p_att_stat.add_argument("-a", "--attachment", help="Attachment UUID (optional)")
    parser_list_att = subparsers.add_parser(
        "list-attachments", help="List all attachments for a support case (shortcut)"
    )
    parser_list_att.add_argument("case_pos", nargs="?", help="Case Number (positional)")
    parser_list_att.add_argument("-c", "--case", help="Case Number")
    parser_list_att.add_argument(
        "--include-deleted", action="store_true", help="Include deleted files"
    )
    parser_list_att.add_argument(
        "--simple-output", action="store_true", help="Disable colored output"
    )
    # Create Subcommand
    parser_create = subparsers.add_parser("create", help="Create a new support case")
    parser_create.add_argument("--product", help="Product name")
    parser_create.add_argument("--version", help="Product version")
    parser_create.add_argument("--summary", help="Case summary")
    parser_create.add_argument("--description-file", help="File containing description")
    parser_create.add_argument(
        "--severity", help="Severity (Low, Normal, High, Urgent)"
    )
    parser_create.add_argument("--type", help="Case type (Standard, Bug)")
    parser_create.add_argument(
        "--attachment",
        action="append",
        help="File to attach (can be used multiple times)",
    )
    parser_create.add_argument(
        "--template",
        action="append",
        help="Use a template (can be used multiple times)",
    )
    parser_create.add_argument(
        "--no-default-template",
        action="store_true",
        help="Ignore default create template from config",
    )
    parser_create.add_argument(
        "--template-var", action="append", help="Variable for template (key=value)"
    )
    parser_create.add_argument(
        "--format",
        choices=["markdown", "plain", "html", "raw"],
        default="markdown",
        help="Format for case description (default: markdown, converted to portal rich text)",
    )

    # Comment Subcommand
    parser_comment = subparsers.add_parser("comment", help="Post a comment to a case")
    parser_comment.add_argument("-c", "--case", required=True, help="Case Number")
    parser_comment.add_argument(
        "-f", "--file", help="File path containing comment (optional)"
    )
    parser_comment.add_argument(
        "-s",
        "--status",
        help="Update case status (e.g. redhat, customer, in_progress, closed, or 'none'/'keep' to leave unchanged)",
    )
    parser_comment.add_argument(
        "--no-status-change",
        "--keep-status",
        action="store_true",
        dest="no_status_change",
        help="Post comment without modifying the case status",
    )
    parser_comment.add_argument(
        "-t",
        "--template",
        action="append",
        help="Use a template (can be used multiple times)",
    )
    parser_comment.add_argument(
        "--template-var", action="append", help="Variable for template (key=value)"
    )
    parser_comment.add_argument(
        "--include-previous-comments",
        type=int,
        nargs="?",
        const=1,
        default=1,
        help="Include the last N comments in the editor header (default: 1)",
    )
    parser_comment.add_argument(
        "--edit",
        action="store_true",
        help="Open editor to edit the content from --file before submitting",
    )
    parser_comment.add_argument(
        "--format",
        choices=["markdown", "plain", "html", "raw"],
        default="markdown",
        help="Format for comment body (default: markdown, converted to portal rich text)",
    )

    # Close Subcommand
    parser_close = subparsers.add_parser(
        "close", help="Close a support case with a comment"
    )
    parser_close.add_argument("-c", "--case", required=True, help="Case Number")
    parser_close.add_argument(
        "-f", "--file", help="File path containing closing comment (optional)"
    )
    parser_close.add_argument(
        "-t",
        "--template",
        action="append",
        help="Use a template (can be used multiple times)",
    )
    parser_close.add_argument(
        "--template-var", action="append", help="Variable for template (key=value)"
    )
    parser_close.add_argument(
        "--include-previous-comments",
        type=int,
        nargs="?",
        const=1,
        default=1,
        help="Include the last N comments in the editor header (default: 1)",
    )
    parser_close.add_argument(
        "--edit",
        action="store_true",
        help="Open editor to edit the content from --file before submitting",
    )
    parser_close.add_argument(
        "--format",
        choices=["markdown", "plain", "html", "raw"],
        default="markdown",
        help="Format for closing comment body (default: markdown, converted to portal rich text)",
    )

    # Apply Subcommand
    parser_apply = subparsers.add_parser(
        "apply", help="Apply supported template fields to a support case"
    )
    parser_apply.add_argument("-c", "--case", required=True, help="Case Number")
    parser_apply.add_argument(
        "-t",
        "--template",
        required=True,
        action="append",
        help="Use a template (can be used multiple times)",
    )
    parser_apply.add_argument(
        "--template-var", action="append", help="Variable for template (key=value)"
    )
    parser_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what changes would be applied without making them",
    )

    # Follow Subcommand
    parser_follow = subparsers.add_parser(
        "follow", help="Follow support case(s) locally to track updates"
    )
    parser_follow.add_argument("cases", nargs="*", help="Case number(s) to follow")
    parser_follow.add_argument(
        "-c",
        "--case",
        action="append",
        help="Case number to follow (can be used multiple times)",
    )
    parser_follow.add_argument(
        "-u",
        "--user",
        help="SSO username (defaults to myself/current authenticated user)",
    )
    parser_follow.add_argument(
        "--sync",
        action="store_true",
        help="Sync followed cases from account before following",
    )
    parser_follow.add_argument("--account", help="Account number to sync from")

    # Following Subcommand
    parser_following = subparsers.add_parser(
        "following",
        help="List support cases currently followed",
    )
    parser_following.add_argument("--account", help="Account Number")
    parser_following.add_argument(
        "--sync",
        action="store_true",
        help="Sync followed cases before listing",
    )
    parser_following.add_argument(
        "-u",
        "--user",
        help="SSO username (defaults to myself/current authenticated user)",
    )
    parser_following.add_argument(
        "-b",
        "--bookmark",
        action="append",
        help="Use a filter bookmark from config (can be used multiple times)",
    )
    parser_following.add_argument(
        "--no-default-bookmark",
        action="store_true",
        help="Ignore default bookmark from config",
    )

    # Unfollow Subcommand
    parser_unfollow = subparsers.add_parser(
        "unfollow",
        help="Unfollow support case(s) locally",
    )
    parser_unfollow.add_argument("cases", nargs="*", help="Case number(s) to unfollow")
    parser_unfollow.add_argument(
        "-c",
        "--case",
        action="append",
        help="Case number to unfollow (can be used multiple times)",
    )
    parser_unfollow.add_argument(
        "-u",
        "--user",
        help="SSO username (defaults to myself/current authenticated user)",
    )

    # Sync Subcommand
    parser_sync = subparsers.add_parser(
        "sync", help="Synchronize tracked cases with remote updates"
    )
    parser_sync.add_argument(
        "-u", "--user", help="SSO username (defaults to current user)"
    )
    parser_sync.add_argument("--account", help="Account Number filter")
    parser_sync.add_argument(
        "--notify",
        action="store_true",
        help="Send desktop notification if updates found",
    )
    parser_sync.add_argument(
        "--install-systemd",
        action="store_true",
        help="Generate and install systemd user service and timer",
    )
    parser_sync.add_argument(
        "--interval",
        default="15m",
        help="Sync interval for systemd timer (default: 15m)",
    )
    parser_sync.add_argument(
        "--simple-output", action="store_true", help="Disable colored output"
    )

    # Updates Subcommand
    parser_updates = subparsers.add_parser(
        "updates", help="Display unread updates and new comments on tracked cases"
    )
    parser_updates.add_argument(
        "-c", "--case", help="Filter updates to a specific case number"
    )
    parser_updates.add_argument(
        "--full",
        action="store_true",
        help="Show full content of new comments (default: excerpt summary)",
    )
    parser_updates.add_argument(
        "--all",
        action="store_true",
        help="Show all tracked cases, including those without new updates",
    )
    parser_updates.add_argument(
        "--mark-read", action="store_true", help="Mark inspected updates as read"
    )
    parser_updates.add_argument(
        "--simple-output", action="store_true", help="Disable colored output"
    )
    parser_updates.add_argument(
        "-u", "--user", help="SSO username (defaults to current user)"
    )

    # Search Subcommand
    parser_search = subparsers.add_parser(
        "search", help="Search support cases and Red Hat Knowledgebase (KCS) solutions"
    )
    parser_search.add_argument(
        "query", help="Search keywords, error message, or case number"
    )
    parser_search.add_argument(
        "-t",
        "--type",
        choices=["cases", "kcs", "all"],
        default="cases",
        help="Search target: 'cases' (support tickets, default), 'kcs' (knowledgebase), or 'all'",
    )
    parser_search.add_argument("-p", "--product", help="Filter by product name")
    parser_search.add_argument(
        "-s",
        "--status",
        help="Filter cases by status (e.g. open, closed, redhat, customer)",
    )
    parser_search.add_argument(
        "--severity", help="Filter cases by severity (e.g. urgent, high, normal, low)"
    )
    parser_search.add_argument("-a", "--account", help="Filter cases by account number")
    parser_search.add_argument(
        "-u", "--owner", help="Filter cases by owner name or SSO"
    )
    parser_search.add_argument(
        "-l",
        "--limit",
        type=int,
        default=20,
        help="Maximum results to return (default: 20)",
    )
    parser_search.add_argument(
        "--local",
        action="store_true",
        help="Search locally cached cases and comments only (offline mode)",
    )
    parser_search.add_argument(
        "-b",
        "--bookmark",
        action="append",
        help="Use a filter bookmark from config (can be used multiple times)",
    )
    parser_search.add_argument(
        "--no-default-bookmark",
        action="store_true",
        help="Ignore default bookmark from config",
    )
    parser_search.add_argument(
        "--language",
        "--lang",
        dest="language",
        help="Filter KCS results by language code (e.g. 'en', 'ja', default: 'en' or from config/bookmark)",
    )
    parser_search.add_argument(
        "-o",
        "--open",
        "--browser",
        action="store_true",
        dest="open",
        help="Open the top result URL in the system web browser",
    )
    parser_search.add_argument(
        "--simple-output", action="store_true", help="Disable colored output"
    )

    # Systemd Subcommand
    parser_systemd = subparsers.add_parser(
        "systemd", help="Manage systemd user timer for periodic case synchronization"
    )
    systemd_sub = parser_systemd.add_subparsers(dest="systemd_action")

    p_sys_install = systemd_sub.add_parser(
        "install", help="Install systemd timer and service"
    )
    p_sys_install.add_argument(
        "--interval",
        default="15m",
        help="Sync interval (e.g. 5m, 15m, 1h, default: 15m)",
    )
    p_sys_install.add_argument(
        "--notify",
        action="store_true",
        help="Enable desktop notifications on new comments/updates",
    )
    p_sys_install.add_argument(
        "--enable",
        "--now",
        action="store_true",
        dest="enable",
        help="Immediately enable and start timer via systemctl",
    )

    systemd_sub.add_parser(
        "uninstall", help="Stop, disable, and remove systemd timer and service"
    )
    systemd_sub.add_parser("status", help="Show systemd timer and service status")

    # install-systemd Shortcut Subcommand
    parser_install_sys = subparsers.add_parser(
        "install-systemd",
        help="Install systemd user timer for periodic case synchronization",
    )
    parser_install_sys.add_argument(
        "--interval",
        default="15m",
        help="Sync interval (e.g. 5m, 15m, 1h, default: 15m)",
    )
    parser_install_sys.add_argument(
        "--notify",
        action="store_true",
        help="Enable desktop notifications on new comments/updates",
    )
    parser_install_sys.add_argument(
        "--enable",
        "--now",
        action="store_true",
        dest="enable",
        help="Immediately enable and start timer via systemctl",
    )

    subparsers.add_parser(
        "install-timer",
        help="Install systemd user timer for periodic case synchronization (alias)",
    )
    # List Templates Subcommand
    subparsers.add_parser(
        "list-templates", help="List all local templates and their details"
    )
    subparsers.add_parser(
        "list-template", help="List all local templates and their details (alias)"
    )

    # TUI Subcommand
    parser_tui = subparsers.add_parser(
        "tui", help="Launch the interactive terminal user interface"
    )
    parser_tui.add_argument(
        "-b", "--bookmark", help="Use a preset bookmark of filters on startup"
    )
    parser_tui.add_argument(
        "--no-default-bookmark",
        action="store_true",
        help="Do not load the default bookmark on startup",
    )
    parser_tui.add_argument(
        "-l",
        "--layout",
        choices=["vertical", "horizontal", "mailbox"],
        help="Split layout: 'horizontal' / 'mailbox' (default, top-and-bottom) or 'vertical' (side-by-side)",
    )
    parser_tui.add_argument(
        "--order",
        choices=["newest", "oldest", "desc", "asc"],
        default=None,
        help="Comment display order: 'newest' (default, matches portal) or 'oldest'",
    )
    parser_tui.add_argument(
        "--newest-first",
        "--reverse",
        action="store_true",
        dest="newest_first",
        help="Display comments in reverse chronological order (newest first, default)",
    )
    parser_tui.add_argument(
        "--oldest-first",
        action="store_true",
        dest="oldest_first",
        help="Display comments in chronological order (oldest first)",
    )
    # Mock Portal Subcommand
    parser_mock_portal = subparsers.add_parser(
        "mock-portal",
        help="Launch a local stateful mock Red Hat Support Portal REST API",
    )
    parser_mock_portal.add_argument(
        "--corpus",
        help="Path to directory for the synthetic support case corpus (defaults to a transient temporary directory)",
    )
    parser_mock_portal.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local TCP port to bind the server to (default: 8080)",
    )
    content_format_parsers = {
        parser_create,
        parser_comment,
        parser_close,
        parser_systemd,
        parser_install_sys,
        p_sys_install,
    }
    for p in set(subparsers.choices.values()):
        p.add_argument(
            "-j", "--json", action="store_true", help="Output in JSON format"
        )
        if p not in content_format_parsers:
            p.add_argument(
                "--format",
                choices=["text", "json", "yaml"],
                default=None,
                help="Output format: 'text', 'json', or 'yaml'",
            )
        p.add_argument(
            "--no-cache", action="store_true", help="Bypass local disk cache"
        )
        if not any(a.dest == "simple_output" for a in p._actions):
            p.add_argument(
                "--simple-output",
                action="store_true",
                help="Disable colored output and visual indicators",
            )
    for att_p in set(att_sub.choices.values()):
        att_p.add_argument(
            "-j", "--json", action="store_true", help="Output in JSON format"
        )
        att_p.add_argument(
            "--format",
            choices=["text", "json", "yaml"],
            default=None,
            help="Output format: 'text', 'json', or 'yaml'",
        )
        att_p.add_argument(
            "--no-cache", action="store_true", help="Bypass local disk cache"
        )
        if not any(a.dest == "simple_output" for a in att_p._actions):
            att_p.add_argument(
                "--simple-output",
                action="store_true",
                help="Disable colored output and visual indicators",
            )

    if argcomplete:
        from rh_support_lib.completers import setup_completers

        setup_completers(parser)
        argcomplete.autocomplete(parser)
    args = parser.parse_args()
    if "--simple-output" in sys.argv or getattr(args, "simple_output", False):
        args.simple_output = True

    if getattr(args, "command", None) not in ("create", "comment", "close"):
        if (
            "--json" in sys.argv
            or "-j" in sys.argv
            or getattr(args, "json", False)
            or getattr(args, "format", None) == "json"
        ):
            args.format = "json"
            args.json = True
        elif getattr(args, "format", None) == "yaml":
            args.format = "yaml"
            args.json = False
        else:
            args.format = "text"
            args.json = False

    config = load_config(args.config_file)
    cache_cfg = config.get("cache") or {}
    no_cache = (
        getattr(args, "no_cache", False)
        or "--no-cache" in sys.argv
        or cache_cfg.get("enabled") is False
        or os.environ.get("RH_NO_CACHE") in ("1", "true", "True")
    )
    if no_cache:
        os.environ["RH_NO_CACHE"] = "1"
        args.no_cache = True
    else:
        args.no_cache = False

    if args.command == "link":
        cmd_link(args)
        sys.exit(0)
    elif args.command == "completion":
        cmd_completion(args)
        sys.exit(0)
    elif args.command in ["list-templates", "list-template"]:
        cmd_list_templates(args, config)
        sys.exit(0)
    elif args.command in ["systemd", "install-systemd", "install-timer"]:
        from rh_support_lib.commands.systemd import cmd_install_systemd, cmd_systemd

        if args.command in ("install-systemd", "install-timer"):
            cmd_install_systemd(args)
        else:
            cmd_systemd(args)
        sys.exit(0)
    elif args.command == "mock-portal":
        from rh_support_lib.synthetic_corpus.server import run_mock_server

        run_mock_server(corpus_dir=args.corpus, port=args.port)
        sys.exit(0)

    debug_file = args.debug_file or config.get("debug_file")
    if args.debug or debug_file:
        enable_debug_logging(log_file=debug_file)
    # Instantiate RedHatAPIClient before running commands
    from rh_support_lib.api import RedHatAPIClient

    if args.command != "tui" and getattr(args, "format", "text") == "text":
        print("Authenticating...")
    api_client = RedHatAPIClient(args.token_file)
    if args.command == "attach":
        cmd_attach(args, api_client)
    elif args.command == "download":
        cmd_download(args, api_client)
    elif args.command in ("attachment", "attachments"):
        cmd_attachment(args, api_client)
    elif args.command == "list-attachments":
        case_val = getattr(args, "case", None) or getattr(args, "case_pos", None)
        if not case_val:
            sys.exit(
                "Error: Case number is required (e.g. list-attachments 12345 or -c 12345)."
            )
        args.case = str(case_val)
        args.attachment_action = "list"
        cmd_attachment(args, api_client)
    elif args.command == "create":
        cmd_create(args, api_client, config)
    elif args.command == "comment":
        cmd_comment(args, api_client)
    elif args.command == "close":
        cmd_close(args, api_client)
    elif args.command == "list":
        cmd_list(args, api_client, config)
    elif args.command == "show":
        cmd_show(args, api_client, config=config)
    elif args.command == "apply":
        cmd_apply(args, api_client, config)
    elif args.command == "follow":
        cmd_follow(args, api_client)
    elif args.command == "following":
        cmd_following(args, api_client, config)
    elif args.command == "unfollow":
        cmd_unfollow(args, api_client)
    elif args.command == "sync":
        cmd_sync(args, api_client, config)
    elif args.command == "updates":
        cmd_updates(args, api_client, config)
    elif args.command == "tui":
        cmd_tui(args, api_client, config)
    elif args.command == "search":
        cmd_search(args, api_client, config)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)
