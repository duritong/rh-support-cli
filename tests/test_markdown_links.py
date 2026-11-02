from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rh_support_lib.commands.show_case import cmd_show
from rh_support_lib.config import ConfigError, load_config
from rh_support_lib.models import Case, CaseComment, CaseDetails
from rh_support_lib.utils import (
    extract_markdown_links,
    format_markdown_footnotes,
    format_markdown_for_links,
    format_markdown_inline,
    render_markdown,
)


class MarkdownLinksTests(unittest.TestCase):
    def test_format_markdown_footnotes(self):
        text = (
            "Check [Red Hat](https://access.redhat.com) and [Red Hat](https://access.redhat.com).\n"
            "Also [Solution](https://access.redhat.com/solutions/123).\n"
            "Code block:\n"
            "```\n"
            "[not a link](https://fake.url)\n"
            "```\n"
            "Inline `[also not link](https://fake.url)`.\n"
            "Direct link: [https://google.com](https://google.com)."
        )
        formatted = format_markdown_footnotes(text)
        self.assertIn("Check Red Hat [1] and Red Hat [1].", formatted)
        self.assertIn("Also Solution [2].", formatted)
        self.assertIn("[not a link](https://fake.url)", formatted)
        self.assertIn("`[also not link](https://fake.url)`", formatted)
        self.assertIn("Direct link: https://google.com.", formatted)
        self.assertIn("**Links:**", formatted)
        self.assertIn("[1] https://access.redhat.com", formatted)
        self.assertIn("[2] https://access.redhat.com/solutions/123", formatted)

    def test_format_markdown_footnotes_no_links(self):
        text = "Just normal text without any links."
        self.assertEqual(format_markdown_footnotes(text), text)

    def test_format_markdown_inline(self):
        text = (
            "Check [Red Hat](https://access.redhat.com).\n"
            "Direct: [https://google.com](https://google.com).\n"
            "Code: `[code link](https://code.url)`"
        )
        formatted = format_markdown_inline(text)
        self.assertIn("Check Red Hat (https://access.redhat.com).", formatted)
        self.assertIn("Direct: https://google.com.", formatted)
        self.assertIn("`[code link](https://code.url)`", formatted)

    def test_format_markdown_for_links_modes(self):
        sample = "See [Portal](https://access.redhat.com)."
        foot, hl_foot = format_markdown_for_links(sample, "footnotes")
        self.assertIn("Portal [1]", foot)
        self.assertFalse(hl_foot)

        inline, hl_inline = format_markdown_for_links(sample, "inline")
        self.assertIn("Portal (https://access.redhat.com)", inline)
        self.assertFalse(hl_inline)

        hidden, hl_hidden = format_markdown_for_links(sample, "hidden")
        self.assertEqual(hidden, sample)
        self.assertTrue(hl_hidden)

    def test_extract_markdown_links(self):
        sample = (
            "Review [Portal](https://access.redhat.com) and [Docs](https://docs.redhat.com).\n"
            "Repeated [Portal](https://access.redhat.com).\n"
            "Bare link: https://github.com/redhat/test.\n"
            "```\nhttps://fake.url\n```"
        )
        links = extract_markdown_links(sample)
        self.assertEqual(
            links,
            [
                ("Portal", "https://access.redhat.com"),
                ("Docs", "https://docs.redhat.com"),
                ("https://github.com/redhat/test", "https://github.com/redhat/test"),
            ],
        )

    def test_render_markdown_returns_rich_renderable(self):
        md = render_markdown("Test [Link](https://example.com)", style="footnotes")
        self.assertFalse(md.hyperlinks)

        md_hidden = render_markdown("Test [Link](https://example.com)", style="hidden")
        self.assertTrue(md_hidden.hyperlinks)

    def test_cmd_show_uses_link_style(self):
        client = Mock()
        case = Case(
            case_number="12345",
            summary="Test Case",
            status="Waiting on Red Hat",
            severity="High",
        )
        details = CaseDetails(
            case=case,
            description="Case description with [Doc Link](https://docs.redhat.com).",
            comments=[
                CaseComment(
                    id="c1",
                    body="Comment with [Solution Link](https://access.redhat.com/solutions/1).",
                    created_date="2026-10-30T10:00:00Z",
                    created_by="associate",
                )
            ],
        )

        with (
            patch("rh_support_lib.commands.show_case.CaseService") as mock_cs_cls,
            patch("rich.console.Console.print") as mock_print,
        ):
            instance = mock_cs_cls.return_value
            instance.get_case_details.return_value = details
            instance.get_case_attachments.return_value = []
            instance.state_service.get_tracked_cases.return_value = {}

            # Test footnotes (default)
            args = SimpleNamespace(
                case="12345",
                no_pager=True,
                simple_output=False,
                export=None,
                mark_read=False,
                order=None,
                newest_first=False,
                oldest_first=False,
                links="footnotes",
            )
            cmd_show(args, client, config={})
            markups = []
            for call in mock_print.call_args_list:
                if not call[0]:
                    continue
                obj = call[0][0]
                if hasattr(obj, "markup"):
                    markups.append(obj.markup)
                elif hasattr(obj, "renderable") and hasattr(obj.renderable, "markup"):
                    markups.append(obj.renderable.markup)
            self.assertTrue(any("Doc Link [1]" in m for m in markups))
            self.assertTrue(any("Solution Link [1]" in m for m in markups))

    def test_config_markdown_links_validation(self):
        # Valid options
        for key in ("markdown_links", "link_style"):
            for style in ("footnotes", "inline", "hidden", "FOOTNOTES"):
                with (
                    patch(
                        "rh_support_lib.config.yaml.safe_load",
                        return_value={key: style},
                    ),
                    patch("builtins.open", mock_open=True),
                ):
                    cfg = load_config("dummy.yaml")
                    self.assertIn(key, cfg)

        # Invalid options
        for bad in ("invalid", 123, True, []):
            with (
                patch(
                    "rh_support_lib.config.yaml.safe_load",
                    return_value={"markdown_links": bad},
                ),
                patch("builtins.open", mock_open=True),
                self.assertRaises(ConfigError),
            ):
                load_config("dummy.yaml")
