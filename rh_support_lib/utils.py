import html
import os
import re
import shlex
import subprocess
import tempfile
from html.parser import HTMLParser

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
        text = "".join(self.result).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text


def clean_comment_body(body: str) -> str:
    """
    Cleans up a case comment or description body:
    1. Unescapes HTML entities (including double-encoded entities from CRM).
    2. Strips outer wrapping <div>...</div> tags (e.g. legacy-comment-plaintext, legacy-comment-markdown).
    3. If the content contains HTML elements, converts them into clean, human-readable Markdown.
    """
    if not body:
        return ""

    text = html.unescape(body)
    if re.search(r"&(?:amp;|lt;|gt;|quot;|#\d+;|#x[0-9a-fA-F]+;)", text):
        text = html.unescape(text)

    stripped = text.strip()
    div_match = re.match(
        r"^<div\b[^>]*>(.*)</div>$", stripped, flags=re.DOTALL | re.IGNORECASE
    )
    if div_match:
        stripped = div_match.group(1).strip()

    if re.search(
        r"<(?:p|h[1-6]|br|ul|ol|li|a|strong|b|em|i|pre|code|blockquote|hr|div|span|table|img|del|s)\b",
        stripped,
        re.IGNORECASE,
    ):
        parser = HTMLToMarkdownParser()
        parser.feed(stripped)
        return parser.get_markdown()

    return stripped


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


def get_status_color(stat_str):
    if not stat_str:
        return None
    s = str(stat_str).lower()
    if "red hat" in s:
        return COLORS.YELLOW
    if "customer" in s:
        return COLORS.GREEN
    if "closed" in s:
        return COLORS.GREY
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
    """
    Removes leading lines that start with '#' (ignoring leading whitespace).
    Stops at the first line that does not start with '#'.
    """
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


def open_editor(case_number, status_label, header_content=None, initial_body=None):
    """
    Opens the system default editor for interactive comment entry.
    Returns the content of the comment and the path to the temporary file.
    """
    editor = os.environ.get("EDITOR", "vi")

    # Create temp file but do NOT delete it automatically here
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".tmp", delete=False) as tf:
        tf.write(f"# Enter comment for Case {case_number}.\n")
        tf.write(f"# Status will be set to: '{status_label}'\n")

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

    try:
        cmd = shlex.split(editor)
        cmd.append(tf_path)
        subprocess.call(cmd)

        with open(tf_path, "r") as f:
            lines = f.readlines()

        content = strip_header_comments(lines)
        return content.strip(), tf_path
    except Exception:
        # If something goes wrong during editing, try to clean up
        if os.path.exists(tf_path):
            os.remove(tf_path)
        raise
