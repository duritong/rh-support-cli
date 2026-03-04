import os
import yaml


def load_config(config_file_arg=None):
    """
    Loads configuration from YAML file.
    Priority:
    1. CLI Argument (--config-file)
    2. Environment Variable (RH_SUPPORT_CONFIG)
    3. Default (~/.config/rh-support-cli/config.yaml)
    """
    config_path = None

    if config_file_arg:
        config_path = config_file_arg
    elif os.environ.get("RH_SUPPORT_CONFIG"):
        config_path = os.environ.get("RH_SUPPORT_CONFIG")
    else:
        config_path = os.path.expanduser("~/.config/rh-support-cli/config.yaml")

    if not config_path or not os.path.isfile(config_path):
        return {}

    try:
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Warning: Failed to load config from {config_path}: {e}")
        return {}
