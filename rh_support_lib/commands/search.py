from __future__ import annotations

import os
import sys
import webbrowser
from typing import Any

from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table
from rich.text import Text

from rh_support_lib.constants import SEVERITY_MAP, STATUS_FILTER_MAP
from rh_support_lib.models import Case, KCSSearchResult
from rh_support_lib.services.search import SearchService
from rh_support_lib.utils import format_structured_output


def cmd_search(args, api_client, config: dict[str, Any] | None = None) -> None:
    config = config or {}

    # 1. Gather Bookmark Filters
    filters: dict[str, Any] = {}
    default_bk = config.get("default_bookmark")
    if default_bk and not getattr(args, "no_default_bookmark", False):
        bks = default_bk if isinstance(default_bk, list) else [default_bk]
        for bk_name in bks:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)

    if getattr(args, "bookmark", None):
        for bk_name in args.bookmark:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)

    query = getattr(args, "query", "") or ""
    search_type = getattr(args, "type", "cases") or "cases"
    search_type = search_type.lower().strip()

    limit = getattr(args, "limit", 20) or 20
    local_only = bool(getattr(args, "local", False))
    open_browser = bool(getattr(args, "open", False) or getattr(args, "browser", False))

    # CLI Overrides take precedence over bookmarks
    product = getattr(args, "product", None) or filters.get("product")
    account = (
        getattr(args, "account", None)
        or filters.get("account")
        or filters.get("accountNumber")
    )
    owner = (
        getattr(args, "owner", None)
        or filters.get("owner")
        or filters.get("ownerSSOName")
    )

    raw_status = getattr(args, "status", None) or filters.get("status")
    mapped_statuses = None
    if raw_status:
        if isinstance(raw_status, str):
            raw_status = [raw_status]
        mapped_statuses = []
        for s in raw_status:
            val = STATUS_FILTER_MAP.get(str(s).lower(), s)
            for v in val.split(","):
                mapped_statuses.append(v.strip())

    raw_sev = getattr(args, "severity", None) or filters.get("severity")
    mapped_sevs = None
    if raw_sev:
        if isinstance(raw_sev, str):
            raw_sev = [raw_sev]
        mapped_sevs = []
        for s in raw_sev:
            mapped_sevs.append(SEVERITY_MAP.get(str(s).lower(), s))

    # Language resolution: CLI flag > bookmark > config > default 'en'
    language = (
        getattr(args, "language", None)
        or filters.get("language")
        or filters.get("kcs_language")
        or config.get("default_kcs_language")
        or config.get("kcs", {}).get("language")
        or "en"
    )
    service = SearchService(api_client)
    cases: list[Case] = []
    kcs: list[KCSSearchResult] = []

    fmt = getattr(args, "format", "text")
    is_structured = fmt in ("json", "yaml") or getattr(args, "json", False)

    try:
        if search_type in ("cases", "case"):
            cases = service.search_cases(
                query=query,
                account=str(account) if account else None,
                status=mapped_statuses,
                severity=mapped_sevs,
                owner=str(owner) if owner else None,
                product=str(product) if product else None,
                limit=limit,
                local_only=local_only,
            )
        elif search_type == "kcs":
            kcs = service.search_kcs(
                query=query,
                product=str(product) if product else None,
                language=str(language) if language else None,
                limit=limit,
            )
        else:  # 'all'
            res = service.search_all(
                query=query,
                product=str(product) if product else None,
                language=str(language) if language else None,
                limit=limit,
                local_only=local_only,
            )
            cases = res.get("cases", [])
            kcs = res.get("kcs", [])
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Search Error: {e}")

    # Structured Output
    if is_structured:
        out_data: dict[str, Any] | list[Any]
        if search_type in ("cases", "case"):
            out_data = cases
        elif search_type == "kcs":
            out_data = kcs
        else:
            out_data = {"cases": cases, "kcs": kcs}
        print(
            format_structured_output(out_data, fmt="yaml" if fmt == "yaml" else "json")
        )
        return

    # Check empty results
    if not cases and not kcs:
        print(f"No results found for query '{query}'.")
        return

    simple = getattr(args, "simple_output", False)
    console_width = None
    if "COLUMNS" in os.environ:
        try:
            console_width = int(os.environ["COLUMNS"])
        except ValueError:
            pass
    if console_width is None and (simple or not sys.stdout.isatty()):
        console_width = 200

    color_sys = (
        None
        if simple
        else ("standard" if os.environ.get("FORCE_COLOR") == "1" else "auto")
    )
    console = Console(
        no_color=simple,
        highlight=not simple,
        width=console_width,
        height=int(os.environ.get("LINES", "100")),
        color_system=color_sys,
        force_terminal=True if os.environ.get("FORCE_COLOR") == "1" else None,
    )

    box_style = None if simple else ROUNDED

    # Render Cases Table
    if cases:
        if search_type == "all":
            console.print(
                "\n[bold cyan]── SUPPORT CASES ──────────────────────────────[/bold cyan]"
            )
        table_c = Table(
            show_header=True,
            header_style="bold magenta" if not simple else "",
            box=box_style,
            pad_edge=False,
        )
        table_c.add_column("CASE #", width=14)
        table_c.add_column("SUMMARY", ratio=1, min_width=30)
        table_c.add_column("STATUS", width=20)
        table_c.add_column("SEVERITY", width=10)
        table_c.add_column("PRODUCT", width=16)
        table_c.add_column("URL", width=46)
        for c in cases:
            num = c.case_number or c.id
            if simple:
                num_str = num
                if getattr(c, "is_followed", False):
                    num_str += " [F]"
                if getattr(c, "has_unread", False):
                    cnt = getattr(c, "unread_count", 0)
                    num_str += f" *({cnt})" if cnt else " *"
                num_col = num_str
            else:
                num_col = Text(num)
                if getattr(c, "is_followed", False):
                    num_col.append(" ★", style="bold yellow")
                if getattr(c, "has_unread", False):
                    cnt = getattr(c, "unread_count", 0)
                    num_col.append(f" ●{cnt}" if cnt else " ●", style="bold cyan")

            case_url = getattr(
                c, "url", f"https://access.redhat.com/support/cases/{num}"
            )
            url_col = case_url if simple else Text(case_url, style=f"link {case_url}")

            table_c.add_row(
                num_col,
                c.summary or "",
                c.status or "",
                c.severity or "",
                c.product or "",
                url_col,
            )
        console.print(table_c)

    # Render KCS Table
    if kcs:
        if search_type == "all":
            console.print(
                "\n[bold cyan]── KNOWLEDGEBASE (KCS) SOLUTIONS ──────────────[/bold cyan]"
            )
        table_k = Table(
            show_header=True,
            header_style="bold green" if not simple else "",
            box=box_style,
            pad_edge=False,
        )
        table_k.add_column("ID", width=12)
        table_k.add_column("TYPE", width=12)
        table_k.add_column("PRODUCT", width=20)
        table_k.add_column("TITLE", ratio=1, min_width=30)
        table_k.add_column("URL", width=46)

        for doc in kcs:
            kcs_url = doc.url or f"https://access.redhat.com/solutions/{doc.id}"
            url_col = kcs_url if simple else Text(kcs_url, style=f"link {kcs_url}")

            table_k.add_row(
                doc.id or "-",
                doc.document_kind or "Solution",
                doc.product or "",
                doc.title or "",
                url_col,
            )
        console.print(table_k)

    # Browser Open Action
    if open_browser:
        target_url = ""
        if kcs:
            target_url = kcs[0].url
        elif cases:
            target_url = cases[0].url

        if target_url:
            print(f"\nOpening {target_url} in browser...")
            webbrowser.open(target_url)
