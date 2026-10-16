from __future__ import annotations

import math
import os
from collections.abc import Mapping

import yaml


class ConfigError(ValueError):
    """An unreadable or invalid configuration, safe to display to the user."""


def _names(value, field):
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list) and all(
        isinstance(name, str) and name.strip() for name in value
    ):
        return value
    raise ConfigError(f"{field}: expected a bookmark name or list of names")


def resolve_drafts_dir(config: dict | None = None) -> str:
    """Resolves the directory used to preserve comment drafts."""
    if config and config.get("drafts_dir"):
        return os.path.expanduser(config["drafts_dir"])
    if os.environ.get("RH_SUPPORT_DRAFTS"):
        return os.path.expanduser(os.environ["RH_SUPPORT_DRAFTS"])
    if os.environ.get("RH_SUPPORT_CONFIG"):
        return os.path.join(
            os.path.dirname(
                os.path.abspath(os.path.expanduser(os.environ["RH_SUPPORT_CONFIG"]))
            ),
            "drafts",
        )
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return os.path.join(os.path.expanduser(xdg_config), "rh-support-cli", "drafts")
    return os.path.expanduser("~/.config/rh-support-cli/drafts")


def _validate_filters(filters, field):
    if not isinstance(filters, Mapping):
        raise ConfigError(f"{field}: expected a mapping")
    for key, value in filters.items():
        if key in ("account", "accountNumber"):
            valid = type(value) in (str, int)
        elif key in ("status", "severity"):
            types = (str,) if key == "status" else (str, int)
            values = value if isinstance(value, list) else [value]
            valid = all(type(item) in types for item in values)
        elif key == "following":
            valid = isinstance(value, bool)
        elif key in ("owner", "ownerSSOName", "product", "user", "language"):
            valid = isinstance(value, str)
        else:
            continue
        if not valid:
            raise ConfigError(f"{field}.{key}: invalid filter type")


def resolve_bookmarks(config, explicit=None, no_default=False, clear=False):
    """Merge selected bookmarks in order (last wins), without I/O or mutation.

    Explicit bookmarks override default fields. ``no_default`` suppresses defaults;
    ``clear`` bypasses all selection and validation and returns an empty dict.
    """
    if clear:
        return {}
    if not isinstance(config, Mapping):
        raise ConfigError("config: expected a mapping")
    names = []
    if not no_default and "default_bookmark" in config:
        names.extend(
            (name, "default_bookmark")
            for name in _names(config["default_bookmark"], "default_bookmark")
        )
    if explicit is not None:
        names.extend((name, "bookmark") for name in _names(explicit, "bookmark"))
    bookmarks = config.get("bookmarks", {})
    if not isinstance(bookmarks, Mapping):
        raise ConfigError("bookmarks: expected a mapping")
    merged = {}
    for name, field in names:
        if name not in bookmarks:
            raise ConfigError(f"{field}: selected name not found in bookmarks")
        filters = bookmarks[name]
        _validate_filters(filters, "bookmarks selection")
        merged.update(filters)
    return merged


def _validate_config(config):
    if not isinstance(config, dict):
        raise ConfigError("root: expected a mapping")
    for section in ("cache", "tui", "kcs", "bookmarks"):
        if section in config and not isinstance(config[section], dict):
            raise ConfigError(f"{section}: expected a mapping")
    for key in (
        "templates_dir",
        "drafts_dir",
        "debug_file",
        "default_create_template",
        "default_kcs_language",
        "comments_order",
        "comment_order",
        "tui_layout",
        "layout",
    ):
        if key in config:
            if not isinstance(config[key], str):
                raise ConfigError(f"{key}: expected a string")
            if key in ("templates_dir", "drafts_dir", "debug_file"):
                config[key] = os.path.expanduser(config[key])
    if "language" in config.get("kcs", {}) and not isinstance(
        config["kcs"]["language"], str
    ):
        raise ConfigError("kcs.language: expected a string")
    cache = config.get("cache", {})
    if "enabled" in cache and not isinstance(cache["enabled"], bool):
        raise ConfigError("cache.enabled: expected a boolean")
    for section, keys in (
        ("cache", ("list_ttl", "case_ttl", "catalog_ttl", "tui_poll_interval")),
        ("tui", ("poll_interval",)),
    ):
        settings = config.get(section, {})
        for key in keys:
            if key not in settings:
                continue
            value = settings[key]
            polling = key in ("poll_interval", "tui_poll_interval")
            try:
                if isinstance(value, bool) or not isinstance(
                    value, (str, int, float) if polling else (str, int)
                ):
                    raise TypeError
                number = float(value) if polling else int(value)
                if number < 0 or (polling and not math.isfinite(number)):
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                expected = (
                    "finite nonnegative number" if polling else "nonnegative integer"
                )
                raise ConfigError(f"{section}.{key}: expected a {expected}") from None
            settings[key] = number
    for name, filters in config.get("bookmarks", {}).items():
        if not isinstance(name, str):
            raise ConfigError("bookmarks: expected string names")
        _validate_filters(filters, f"bookmarks.{name}")
    if "default_bookmark" in config:
        resolve_bookmarks(config)
    return config


def load_config(config_file_arg=None):
    """Load and validate CLI > RH_SUPPORT_CONFIG > optional default YAML.

    Only an absent default file is optional. All other read, parse, and known
    schema failures raise ConfigError without exposing YAML contents.
    """
    env_path = os.environ.get("RH_SUPPORT_CONFIG")
    explicit = config_file_arg is not None or bool(env_path)
    selected = (
        config_file_arg
        if config_file_arg is not None
        else env_path or "~/.config/rh-support-cli/config.yaml"
    )
    try:
        config_path = os.path.expanduser(os.fspath(selected))
    except (TypeError, ValueError):
        raise ConfigError("config path: expected a filesystem path") from None
    if not config_path:
        raise ConfigError("config path: expected a nonempty filesystem path")
    try:
        with open(config_path, "r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except FileNotFoundError:
        if not explicit and not os.path.lexists(config_path):
            return {}
        raise ConfigError(f"{config_path}: config file not found") from None
    except (OSError, ValueError):
        raise ConfigError(f"{config_path}: config file cannot be read") from None
    except (yaml.YAMLError, RecursionError):
        raise ConfigError(f"{config_path}: invalid YAML") from None
    try:
        return _validate_config(config)
    except ConfigError as exc:
        raise ConfigError(f"{config_path}: {exc}") from None
