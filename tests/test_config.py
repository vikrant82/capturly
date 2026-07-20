"""Tests for YAML configuration file support."""

import os
import tempfile

from capturly import config


def test_load_config_basic():
    """Load a basic capturly.yaml config file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("mode: log\nbackend: https://api.openai.com\nport: 8080\n")
        f.flush()
        try:
            result = config.load_config(f.name)
            assert result["mode"] == "log"
            assert result["backend"] == "https://api.openai.com"
            assert result["port"] == 8080
        finally:
            os.unlink(f.name)


def test_load_config_env_var_interpolation():
    """Environment variables in ${VAR} syntax are interpolated."""
    os.environ["TEST_BACKEND_URL"] = "https://api.example.com"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("backend: ${TEST_BACKEND_URL}\nmode: record\n")
        f.flush()
        try:
            result = config.load_config(f.name)
            assert result["backend"] == "https://api.example.com"
        finally:
            os.unlink(f.name)
            del os.environ["TEST_BACKEND_URL"]


def test_load_config_env_var_with_default():
    """Environment variables with ${VAR:-default} use default when unset."""
    if "UNSET_VAR_XYZ" in os.environ:
        del os.environ["UNSET_VAR_XYZ"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("port: ${UNSET_VAR_XYZ:-9999}\n")
        f.flush()
        try:
            result = config.load_config(f.name)
            assert result["port"] == 9999
        finally:
            os.unlink(f.name)


def test_load_config_nested_keys():
    """Nested YAML keys are flattened with dot notation."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("dashboard:\n  enabled: true\n  port: 9090\nlog:\n  combine_chunks: true\n")
        f.flush()
        try:
            result = config.load_config(f.name)
            assert result["dashboard.enabled"] is True
            assert result["dashboard.port"] == 9090
            assert result["log.combine_chunks"] is True
        finally:
            os.unlink(f.name)


def test_load_config_missing_file():
    """Missing config file returns empty dict."""
    result = config.load_config("/nonexistent/path/capturly.yaml")
    assert result == {}


def test_load_config_invalid_yaml():
    """Invalid YAML returns empty dict gracefully."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("{{invalid: yaml: [unclosed\n")
        f.flush()
        try:
            result = config.load_config(f.name)
            assert result == {}
        finally:
            os.unlink(f.name)


def test_find_config_file_explicit():
    """Explicit --config path is used directly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("mode: hybrid\n")
        f.flush()
        try:
            result = config.find_config_file(explicit_path=f.name)
            assert result == f.name
        finally:
            os.unlink(f.name)


def test_find_config_file_cwd():
    """Finds capturly.yaml in current directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "capturly.yaml")
        with open(config_path, "w") as f:
            f.write("mode: log\n")
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            result = config.find_config_file()
            # macOS may resolve symlinks differently
            assert result is not None
            assert os.path.realpath(result) == os.path.realpath(config_path)
        finally:
            os.chdir(old_cwd)


def test_find_config_file_none():
    """Returns None when no config file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            result = config.find_config_file()
            assert result is None
        finally:
            os.chdir(old_cwd)


def test_merge_config_with_args():
    """CLI args override config file values."""
    import argparse

    cfg = {"mode": "log", "port": 8080, "backend": "https://config.example.com"}
    # port=7777 is non-default, so CLI wins; mode="record" is non-default, CLI wins
    args = argparse.Namespace(mode="record", port=7777, backend=None, delay=0)

    merged = config.merge_config_with_args(cfg, args)

    assert merged.mode == "record"  # CLI wins (non-default)
    assert merged.port == 7777  # CLI wins (non-default)
    assert merged.backend == "https://config.example.com"  # config fills None
    assert merged.delay == 0  # unchanged (no config key)


def test_merge_config_fills_defaults():
    """Config fills values that are at their CLI defaults."""
    import argparse

    cfg = {"mode": "log", "port": 8080}
    # port=9999 is the default, so config overrides it
    args = argparse.Namespace(mode="replay", port=9999, backend=None, delay=0)

    merged = config.merge_config_with_args(cfg, args)

    assert merged.mode == "log"  # config wins (CLI was at default)
    assert merged.port == 8080  # config wins (CLI was at default)
