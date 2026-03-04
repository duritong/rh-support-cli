import os
import sys
import yaml

try:
    import jinja2
    import dateparser
except ImportError:
    jinja2 = None
    dateparser = None


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
        path = os.path.join(self.templates_dir, f"{name}.yaml")
        if not os.path.isfile(path):
            if os.path.isfile(name):
                path = name
            else:
                return None

        try:
            with open(path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Warning: Failed to load template {path}: {e}")
            return None

    def _merge_dicts(self, base, update):
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._merge_dicts(base[k], v)
            else:
                base[k] = v
        return base

    def process(self, template_names, template_vars):
        if not jinja2:
            sys.exit(
                "Error: 'jinja2' is required for templates. Please install it: pip install jinja2"
            )

        merged_data = {}
        for name in template_names:
            merged_data = self._merge_recursive(merged_data, name)

        context = merged_data.copy()
        context.update(template_vars)
        context["currentDoc"] = merged_data

        return self._render_recursive(merged_data, context)

    def _merge_recursive(self, base, template_name):
        raw = self._load_raw_template(template_name)
        if not raw:
            print(f"Warning: Template '{template_name}' not found.")
            return base

        includes = raw.pop("include_templates", [])
        if isinstance(includes, str):
            includes = [includes]

        for inc in includes:
            base = self._merge_recursive(base, inc)

        return self._merge_dicts(base, raw)

    def _render_recursive(self, data, context):
        if isinstance(data, dict):
            return {k: self._render_recursive(v, context) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._render_recursive(i, context) for i in data]
        elif isinstance(data, str):
            try:
                env = jinja2.Environment()
                env.filters["parse_date"] = self._parse_date
                tmpl = env.from_string(data)
                return tmpl.render(**context)
            except Exception:
                # print(f"Warning: Failed to render '{data}': {e}") # formatting noise
                return data
        else:
            return data
