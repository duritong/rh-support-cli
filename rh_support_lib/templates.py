from __future__ import annotations

import os

import yaml

try:
    import dateparser
    import jinja2
except ImportError:
    jinja2 = None
    dateparser = None


def resolve_templates_dir(config: dict | None = None) -> str:
    """Resolves the directory containing case YAML templates."""
    if config and config.get("templates_dir"):
        return os.path.expanduser(config["templates_dir"])
    if os.environ.get("RH_SUPPORT_TEMPLATES"):
        return os.path.expanduser(os.environ["RH_SUPPORT_TEMPLATES"])
    if os.environ.get("RH_SUPPORT_CONFIG"):
        return os.path.join(
            os.path.dirname(
                os.path.abspath(os.path.expanduser(os.environ["RH_SUPPORT_CONFIG"]))
            ),
            "templates",
        )
    return os.path.expanduser("~/.config/rh-support-cli/templates")


class TemplateEngine:
    def __init__(self, templates_dir):
        self.templates_dir = templates_dir

    def _parse_date(self, value):
        if not dateparser:
            return value
        dt = dateparser.parse(str(value))
        if dt:
            return dt.strftime("%d-%m-%Y %H:%M:%S")
        return value

    def _load_raw_template(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Template names must be nonempty strings.")
        path = os.path.join(self.templates_dir, f"{name}.yaml")
        if not os.path.isfile(path):
            if os.path.isfile(name):
                path = name
            else:
                raise ValueError(f"Template '{name}' not found.")

        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError, UnicodeError):
            raise ValueError(
                f"Failed to load template '{path}': unreadable or invalid YAML."
            ) from None
        data = {} if data is None else data
        if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
            raise ValueError(
                f"Template '{path}' must contain a mapping with string field names."
            )
        return data

    def _merge_dicts(self, base, update):
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._merge_dicts(base[k], v)
            else:
                base[k] = v
        return base

    def process(self, template_names, template_vars):
        if not jinja2:
            raise ValueError(
                "Template rendering requires 'jinja2'. Please install it: pip install jinja2"
            )
        if isinstance(template_names, str):
            template_names = [template_names]
        if (
            not isinstance(template_names, (list, tuple))
            or not isinstance(template_vars, dict)
            or any(
                not isinstance(name, str) or not name.strip() for name in template_names
            )
        ):
            raise ValueError(
                "Template names must be a list and template variables a mapping."
            )

        merged_data = {}
        for name in template_names:
            merged_data = self._merge_recursive(merged_data, name)

        context = merged_data.copy()
        context.update(template_vars)
        context["currentDoc"] = merged_data

        try:
            return self._render_recursive(merged_data, context)
        except RecursionError as e:
            raise ValueError("Template data is recursive or nested too deeply.") from e

    def _merge_recursive(self, base, template_name, seen=None):
        if seen is None:
            seen = set()

        if template_name in seen:
            raise ValueError(
                f"Circular template dependency detected for '{template_name}'."
            )
        if len(seen) >= 100:
            raise ValueError("Template include nesting exceeds 100 levels.")

        seen.add(template_name)

        raw = self._load_raw_template(template_name)

        includes = raw.pop("include_templates", [])
        if isinstance(includes, str):
            includes = [includes]
        if not isinstance(includes, list) or any(
            not isinstance(name, str) or not name.strip() for name in includes
        ):
            raise ValueError(
                f"Template '{template_name}' include_templates must be a name or list of names."
            )

        for inc in includes:
            base = self._merge_recursive(base, inc, seen.copy())

        return self._merge_dicts(base, raw)

    def _render_recursive(self, data, context, path="root"):
        if isinstance(data, dict):
            return {
                k: self._render_recursive(v, context, f"{path}.{k}")
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [
                self._render_recursive(item, context, f"{path}[{index}]")
                for index, item in enumerate(data)
            ]
        elif isinstance(data, str):
            try:
                env = jinja2.Environment(undefined=jinja2.StrictUndefined)
                env.filters["parse_date"] = self._parse_date
                tmpl = env.from_string(data)
                return tmpl.render(**context)
            except Exception as e:
                raise ValueError(
                    f"Failed to render template field '{path}': {e}"
                ) from e
        else:
            return data
