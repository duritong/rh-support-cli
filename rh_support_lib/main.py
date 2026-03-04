import argparse
import sys
from rh_support_lib.api import get_access_token, enable_debug_logging
from rh_support_lib.config import load_config
from rh_support_lib.commands.list_cases import cmd_list
from rh_support_lib.commands.show_case import cmd_show
from rh_support_lib.commands.create_case import cmd_create
from rh_support_lib.commands.actions import (
    cmd_attach,
    cmd_comment,
    cmd_link,
    cmd_completion,
)

try:
    import argcomplete
except ImportError:
    argcomplete = None


def main():
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
        "--simple-output",
        action="store_true",
        help="Disable colored output and visual indicators",
    )
    parser.add_argument("--config-file", help="Path to configuration file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--debug-file",
        help="Log debug output to file (implies --debug, disables truncation)",
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
        help="Filter by status. Maps 'customer' -> 'Waiting on Customer', 'redhat' -> 'Waiting on Red Hat', 'closed' -> 'Closed', 'open' -> 'Open'. Default: 'Open'. (can be used multiple times)",
    )
    parser_list.add_argument(
        "--severity",
        action="append",
        help="Filter by severity. Values: Low, Normal, High, Urgent. (can be used multiple times)",
    )
    parser_list.add_argument("--owner", help="Filter by owner")
    parser_list.add_argument(
        "--bookmark",
        action="append",
        help="Use a filter bookmark from config (can be used multiple times)",
    )
    parser_list.add_argument(
        "--no-default-bookmark",
        action="store_true",
        help="Ignore default bookmark from config",
    )

    # Show Subcommand
    parser_show = subparsers.add_parser("show", help="Show case details")
    parser_show.add_argument("-c", "--case", required=True, help="Case Number")
    parser_show.add_argument(
        "--no-pager", action="store_true", help="Disable pager output"
    )

    # Link Subcommand
    parser_link = subparsers.add_parser("link", help="Get the browser URL for a case")
    parser_link.add_argument("-c", "--case", required=True, help="Case Number")

    # Completion Subcommand
    subparsers.add_parser("completion", help="Print bash completion script")

    # Attach Subcommand
    parser_attach = subparsers.add_parser("attach", help="Attach a file to a case")
    parser_attach.add_argument("-c", "--case", required=True, help="Case Number")
    parser_attach.add_argument(
        "-f",
        "--file",
        required=True,
        action="append",
        help="File path to attach (can be used multiple times)",
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
        "--template-var", action="append", help="Variable for template (key=value)"
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
        choices=["redhat", "customer", "closed"],
        help="Update case status (default: redhat)",
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

    if argcomplete:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()

    if args.command == "link":
        cmd_link(args)
        sys.exit(0)
    elif args.command == "completion":
        cmd_completion(args)
        sys.exit(0)

    if args.debug or args.debug_file:
        enable_debug_logging(log_file=args.debug_file)

    # Authenticate before running commands
    print("Authenticating...")
    token = get_access_token(args.token_file)

    if args.command == "attach":
        cmd_attach(args, token)
    elif args.command == "create":
        cmd_create(args, token)
    elif args.command == "comment":
        cmd_comment(args, token)
    elif args.command == "list":
        config = load_config(args.config_file)
        cmd_list(args, token, config)
    elif args.command == "show":
        cmd_show(args, token)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)
