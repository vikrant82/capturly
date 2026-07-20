"""YAML configuration file loading with environment variable interpolation."""

import os
import re
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

# CLI argument defaults — used to detect whether the user explicitly set a value.
_ARG_DEFAULTS = {
    "mode": "replay",
    "port": 9999,
    "host": "0.0.0.0",
    "delay": 0,
    "combine_chunks": False,
    "dashboard": False,
    "dashboard_port": 9090,
}


def _coerce_type(value: str) -> Any:
    """Attempt to coerce a string to int, float, or bool for config values."""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _interpolate_env(value: str) -> Any:
    """Replace ${VAR} and ${VAR:-default} patterns with environment values.

    If the entire string is a single env var reference, the result is
    type-coerced (int, bool, etc.). Otherwise returns the interpolated string.
    """
    # If the whole value is a single ${...} reference, coerce the result
    full_match = _ENV_VAR_PATTERN.fullmatch(value)
    if full_match:
        expr = full_match.group(1)
        if ":-" in expr:
            var_name, default = expr.split(":-", 1)
            resolved = os.environ.get(var_name, default)
        else:
            resolved = os.environ.get(expr, value)
        return _coerce_type(resolved)

    def _replace(match: re.Match) -> str:
        expr = match.group(1)
        if ":-" in expr:
            var_name, default = expr.split(":-", 1)
            return os.environ.get(var_name, default)
        return os.environ.get(expr, match.group(0))

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _interpolate_recursive(data: Any) -> Any:
    """Recursively interpolate environment variables in config values."""
    if isinstance(data, str):
        return _interpolate_env(data)
    if isinstance(data, dict):
        return {k: _interpolate_recursive(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_interpolate_recursive(item) for item in data]
    return data


def _flatten(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten nested dicts into dot-notation keys."""
    result: Dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        else:
            result[full_key] = value
    return result


def load_config(path: str) -> Dict[str, Any]:
    """Load and parse a YAML config file with env var interpolation.

    Returns a flat dict with dot-notation keys for nested values.
    Returns an empty dict if the file is missing, unreadable, or invalid YAML.

    Args:
        path: Absolute or relative path to the YAML config file.

    Returns:
        Flat dict of config values, or {} on any failure.
    """
    if yaml is None:
        return {}

    if not os.path.isfile(path):
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    interpolated = _interpolate_recursive(raw)
    return _flatten(interpolated)


def find_config_file(explicit_path: Optional[str] = None) -> Optional[str]:
    """Locate the config file using priority: explicit > cwd > home.

    Args:
        explicit_path: Path from --config flag, if provided.

    Returns:
        Path to the config file, or None if not found.
    """
    if explicit_path:
        return explicit_path if os.path.isfile(explicit_path) else None

    cwd_path = os.path.join(os.getcwd(), "capturly.yaml")
    if os.path.isfile(cwd_path):
        return cwd_path

    home_path = os.path.join(os.path.expanduser("~"), ".capturly", "config.yaml")
    if os.path.isfile(home_path):
        return home_path

    return None


def merge_config_with_args(cfg: Dict[str, Any], args: Any) -> Any:
    """Merge config file values into parsed CLI args. CLI values win.

    For each config key, if the corresponding arg attribute is None or equals
    its default value, the config value is applied. CLI-explicit values are
    never overridden.

    Args:
        cfg: Flat config dict from load_config().
        args: Parsed argparse.Namespace.

    Returns:
        The args namespace with config values merged in.
    """
    # Map flat config keys to arg attribute names
    key_map = {
        "mode": "mode",
        "backend": "backend",
        "port": "port",
        "host": "host",
        "delay": "delay",
        "recordings_dir": "recordings_dir",
        "combine_chunks": "combine_chunks",
        "log.combine_chunks": "combine_chunks",
        "dashboard.enabled": "dashboard",
        "dashboard.port": "dashboard_port",
    }

    for cfg_key, arg_name in key_map.items():
        if cfg_key not in cfg:
            continue
        if not hasattr(args, arg_name):
            continue

        current = getattr(args, arg_name)
        default = _ARG_DEFAULTS.get(arg_name)

        # Only apply config if CLI didn't explicitly set the value
        if current is None or current == default:
            setattr(args, arg_name, cfg[cfg_key])

    return args
