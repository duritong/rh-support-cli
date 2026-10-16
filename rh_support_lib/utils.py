from __future__ import annotations

import html
import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import yaml

from rh_support_lib.constants import COLORS


class HTMLToMarkdownParser(HTMLParser):
    """Converts HTML formatted comment bodies into clean Markdown."""

    def __init__(self):
        super().__init__()
        self.result = []
        self.tag_stack = []
        self.list_stack = []
        self.current_href = None
        self.current_link_text = []
        self.preformatted_ranges = []
        self.preformatted_start = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_dict = dict(attrs)
        self.tag_stack.append((tag, attr_dict))

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._ensure_newline(2)
            self.result.append("#" * level + " ")
        elif tag in ("p", "blockquote"):
            self._ensure_newline(2)
            if tag == "blockquote":
                self.result.append("> ")
        elif tag in ("div", "tr", "thead", "tbody"):
            self._ensure_newline(1)
        elif tag == "br":
            self.result.append("\n")
        elif tag in ("strong", "b"):
            self.result.append("**")
        elif tag in ("em", "i"):
            self.result.append("*")
        elif tag in ("del", "s", "strike"):
            self.result.append("~~")
        elif tag == "code":
            if not self._in_tag("pre"):
                self.result.append("`")
        elif tag == "pre":
            self._ensure_newline(2)
            self.result.append("```\n")
            self.preformatted_start = len(self.result)
        elif tag == "ul":
            self._ensure_newline(1)
            self.list_stack.append(("ul", 0))
        elif tag == "ol":
            self._ensure_newline(1)
            self.list_stack.append(("ol", 1))
        elif tag == "li":
            self._ensure_newline(1)
            indent = "  " * max(0, len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1][0] == "ol":
                count = self.list_stack[-1][1]
                self.result.append(f"{indent}{count}. ")
                self.list_stack[-1] = ("ol", count + 1)
            else:
                self.result.append(f"{indent}* ")
        elif tag == "a":
            self.current_href = attr_dict.get("href")
            self.current_link_text = []
        elif tag == "img":
            alt = attr_dict.get("alt", "image")
            src = attr_dict.get("src", "")
            if src:
                self.result.append(f"![{alt}]({src})")
        elif tag == "hr":
            self._ensure_newline(2)
            self.result.append("---\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.tag_stack and self.tag_stack[-1][0] == tag:
            self.tag_stack.pop()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"):
            self._ensure_newline(2)
        elif tag in ("div", "li"):
            self._ensure_newline(1)
        elif tag in ("strong", "b"):
            self.result.append("**")
        elif tag in ("em", "i"):
            self.result.append("*")
        elif tag in ("del", "s", "strike"):
            self.result.append("~~")
        elif tag == "code":
            if not self._in_tag("pre"):
                self.result.append("`")
        elif tag == "pre":
            if self.preformatted_start is not None:
                self.preformatted_ranges.append(
                    (self.preformatted_start, len(self.result))
                )
                self.preformatted_start = None
            self._ensure_newline(1)
            self.result.append("```\n\n")
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self._ensure_newline(2)
        elif tag == "a":
            href = self.current_href
            text = "".join(self.current_link_text).strip()
            self.current_href = None
            self.current_link_text = []
            if href:
                if href.startswith("mailto:") and text == href[7:] or text == href:
                    self.result.append(text)
                elif text:
                    self.result.append(f"[{text}]({href})")
                else:
                    self.result.append(href)

    def handle_data(self, data):
        if self.current_href is not None:
            self.current_link_text.append(data)
        else:
            self.result.append(data)

    def _ensure_newline(self, count):
        text = "".join(self.result)
        current_newlines = 0
        for ch in reversed(text):
            if ch == "\n":
                current_newlines += 1
            elif not ch.isspace():
                break
        needed = max(0, count - current_newlines)
        if text:
            self.result.append("\n" * needed)

    def _in_tag(self, tag_name):
        return any(t == tag_name for t, _ in self.tag_stack)

    def get_markdown(self):
        parts = []
        end = 0
        for start, stop in self.preformatted_ranges:
            parts.append(re.sub(r"\n{3,}", "\n\n", "".join(self.result[end:start])))
            parts.append("".join(self.result[start:stop]))
            end = stop
        parts.append(re.sub(r"\n{3,}", "\n\n", "".join(self.result[end:])))
        return "".join(parts).strip()


def clean_comment_body(body: str) -> str:
    """
    Cleans up a case comment or description body:
    1. Decodes up to two transport-encoding layers to recognize CRM HTML.
    2. Strips outer wrapping <div>...</div> tags (e.g. legacy-comment-plaintext, legacy-comment-markdown).
    3. If the content contains HTML elements, converts them into clean, human-readable Markdown.
    """
    if not body:
        return ""

    html_elements = r"<(?:p|h[1-6]|br|ul|ol|li|a|strong|b|em|i|pre|code|blockquote|hr|div|span|table|img|del|s)\b"
    text = body
    # Stop before decoding HTML text entities; the parser must see them as data.
    for _ in range(2):
        if re.search(html_elements, text, re.IGNORECASE):
            break
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded

    stripped = text.strip()
    div_match = re.match(
        r"^<div\b[^>]*>(.*)</div>$", stripped, flags=re.DOTALL | re.IGNORECASE
    )
    if div_match:
        stripped = div_match.group(1).strip()

    if re.search(html_elements, stripped, re.IGNORECASE):
        parser = HTMLToMarkdownParser()
        parser.feed(stripped)
        return parser.get_markdown()

    return html.unescape(stripped) if div_match else stripped


def colorize(text, color, simple_output=False):
    if simple_output or not color:
        return text
    return f"{color}{text}{COLORS.RESET}"


def get_severity_color(sev_str):
    if not sev_str:
        return None
    s = str(sev_str).lower()
    if "1" in s or "urgent" in s:
        return COLORS.RED + COLORS.BOLD
    if "2" in s or "high" in s:
        return COLORS.RED
    if "3" in s or "normal" in s or "medium" in s:
        return COLORS.YELLOW
    if "4" in s or "low" in s:
        return COLORS.GREEN
    return None


def select_from_list(prompt, options, key="name"):
    """
    Prompts user to select an item from a list of dicts.
    Returns the selected item (dict).
    """
    if not options:
        return None

    print(f"\n{prompt}:")
    for idx, item in enumerate(options):
        print(f"  {idx + 1}. {item.get(key, 'Unknown')}")

    while True:
        try:
            choice = input(f"Select (1-{len(options)}): ")
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print("Invalid selection. Please try again.")


def prompt_text(prompt, default=None):
    d_str = f" [{default}]" if default else ""
    val = input(f"{prompt}{d_str}: ").strip()
    return val if val else default


def strip_header_comments(lines):
    """Removes generated editor guidance header comments (# Enter comment..., # Leading lines...).

    If a generated boundary marker is present, strips only up to that marker,
    preserving any legitimate Markdown headings in user content below it.
    Otherwise falls back to removing leading lines starting with '#'.
    """
    boundary_marker = "# Leading lines starting with '#' will be ignored."
    found_boundary = False
    boundary_idx = -1
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            break
        if line.strip() == boundary_marker:
            found_boundary = True
            boundary_idx = i
            break

    if found_boundary:
        content_lines = lines[boundary_idx + 1 :]
        start_idx = 0
        while start_idx < len(content_lines) and not content_lines[start_idx].strip():
            start_idx += 1
        return "".join(content_lines[start_idx:])

    first_content_idx = 0
    found_content = False
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            first_content_idx = i
            found_content = True
            break

    if not found_content:
        return ""

    return "".join(lines[first_content_idx:])


class EditorError(RuntimeError):
    """Editor failure with a safe reason and the path to the recoverable draft."""

    def __init__(self, draft_path: str, reason: str):
        self.draft_path = os.fspath(draft_path)
        self.reason = reason
        super().__init__(f"{reason} Draft path: {self.draft_path}")


def edit_file(path: str) -> None:
    """Edit an existing file once; failures never remove the draft or approve it."""
    path = os.fspath(path)
    try:
        cmd = shlex.split(os.environ.get("EDITOR", "vi"))
    except ValueError:
        raise EditorError(path, "Invalid EDITOR command; check quoting.") from None
    if not cmd or not cmd[0] or any("\x00" in arg for arg in cmd):
        raise EditorError(path, "Invalid EDITOR command; specify an executable.")

    try:
        returncode = subprocess.call(cmd + [path])
    except FileNotFoundError:
        raise EditorError(
            path, "Editor executable not found; check EDITOR and PATH."
        ) from None
    except (OSError, ValueError):
        raise EditorError(
            path, "Could not launch editor; check EDITOR and executable permissions."
        ) from None
    if returncode != 0:
        raise EditorError(
            path, f"Editor exited unsuccessfully (exit status {returncode})."
        )


def open_editor(case_number, status_label, header_content=None, initial_body=None):
    """
    Opens the system default editor for interactive comment entry.
    Returns the content (possibly empty) and the path to the temporary file.
    Raises EditorError on editing or reading failure, preserving the draft.
    """
    from rh_support_lib.config import resolve_drafts_dir

    drafts_dir = resolve_drafts_dir()
    os.makedirs(drafts_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w+",
        dir=drafts_dir,
        prefix=f"comment_draft_{case_number}_",
        suffix=".txt",
        delete=False,
    ) as tf:
        tf.write(f"# Enter comment for Case {case_number}.\n")
        if status_label:
            tf.write(f"# Status will be set to: '{status_label}'\n")
        else:
            tf.write("# Status will remain unchanged.\n")
        if header_content:
            tf.write("#\n# --- PREVIOUS COMMENTS ---\n")
            for line in header_content.splitlines():
                tf.write(f"# {line}\n")
            tf.write("# -------------------------\n")

        tf.write("# Leading lines starting with '#' will be ignored.\n\n")

        if initial_body:
            tf.write(initial_body)
            if not initial_body.endswith("\n"):
                tf.write("\n")

        tf_path = tf.name

    edit_file(tf_path)
    try:
        with open(tf_path, "r") as f:
            lines = f.readlines()
    except (OSError, UnicodeError):
        raise EditorError(
            tf_path, "Could not read edited draft; check file permissions and encoding."
        ) from None

    content = strip_header_comments(lines)
    return content.strip(), tf_path


def format_structured_output(data: Any, fmt: str = "json") -> str:
    """Formats structured data (dataclasses, lists, dicts) as JSON or YAML."""

    def _convert(obj: Any) -> Any:
        if is_dataclass(obj) and not isinstance(obj, type):
            return {k: _convert(v) for k, v in asdict(obj).items()}
        if isinstance(obj, (list, tuple, set)):
            return [_convert(x) for x in obj]
        if isinstance(obj, dict):
            return {str(k): _convert(v) for k, v in obj.items()}
        return obj

    converted = _convert(data)
    if fmt == "yaml":
        return yaml.dump(converted, sort_keys=False, default_flow_style=False)
    return json.dumps(converted, indent=2, default=str, allow_nan=False)


def parse_iso_datetime(dt_str: str | None) -> datetime | None:
    """Parses an ISO 8601 date string with variable formats into a UTC-aware datetime."""
    if not dt_str:
        return None
    s = str(dt_str).strip()
    if not s:
        return None
    # Normalize trailing Z
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    # Handle +0000 / -0500 without colon in offset
    elif len(s) >= 5 and (s[-5] in ("+", "-")) and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def is_comment_newer(
    created_date: str | None,
    comment_id: str | None,
    checkpoint_date: str | None,
    checkpoint_id: str | None,
) -> bool:
    """Determines whether a comment is newer than the checkpoint.

    Returns True if:
    1. The comment's created_date is strictly greater than checkpoint_date (parsed UTC datetimes).
    2. Or if parsing fails but created_date > checkpoint_date lexicographically.
    3. Or if no checkpoint_date is present, but checkpoint_id is present and comment_id != checkpoint_id.
    4. Or if neither checkpoint_date nor checkpoint_id is set (untracked/unread history).
    """
    # 1. Compare parsed UTC datetimes if checkpoint_date is provided
    c_dt = parse_iso_datetime(created_date)
    cp_dt = parse_iso_datetime(checkpoint_date)
    if c_dt and cp_dt:
        return c_dt > cp_dt

    # 2. String comparison fallback if dates are present but cannot be parsed as ISO
    if created_date and checkpoint_date:
        return created_date > checkpoint_date

    # 3. ID comparison fallback when no checkpoint date is available
    if checkpoint_id and comment_id:
        return comment_id != checkpoint_id

    # 4. If neither checkpoint date nor checkpoint ID is set, all comments are unread
    return bool(not checkpoint_date and not checkpoint_id)


def format_body_for_portal(body: str | None, fmt: str = "markdown") -> str:
    """Formats comment body or case description for Red Hat Support Portal.

    The portal's Salesforce-based CRM stores and renders Body__c/Description as rich text (HTML).
    Raw plain-text newlines collapse into whitespace unless formatted with HTML tags.

    Supported formats:
    - 'markdown' (default): Converts GFM Markdown to HTML with soft breaks enabled (<p>, <br>, <pre><code>).
    - 'plain': Escapes HTML entities and converts newlines to <p> and <br /> tags without markdown formatting.
    - 'html': Verbatim HTML passthrough (stripped).
    - 'raw': Verbatim unformatted string passthrough without any HTML conversion.
    """
    if not body:
        return ""
    body_str = str(body)
    fmt_norm = (fmt or "markdown").lower().strip()

    if fmt_norm == "raw":
        return body_str

    if fmt_norm == "html":
        return body_str.strip()

    if fmt_norm == "plain":
        escaped = html.escape(body_str)
        paragraphs = [
            p.replace("\n", "<br />").strip()
            for p in escaped.split("\n\n")
            if p.strip()
        ]
        if not paragraphs:
            return ""
        return "".join(f"<p>{p}</p>" for p in paragraphs)

    # Default: markdown
    from markdown_it import MarkdownIt

    md = MarkdownIt("gfm-like", {"breaks": True, "html": True})
    rendered = md.render(body_str).strip()
    return rendered
