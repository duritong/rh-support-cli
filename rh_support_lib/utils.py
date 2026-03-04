import os
import tempfile
import subprocess
import shlex
from rh_support_lib.constants import COLORS


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
