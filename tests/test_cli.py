"""Tests for the capturly CLI."""

import subprocess
import sys


def test_cli_help():
    """CLI --help shows usage information."""
    result = subprocess.run(
        [sys.executable, "-m", "capturly", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "record" in result.stdout
    assert "replay" in result.stdout
    assert "hybrid" in result.stdout
    assert "log" in result.stdout


def test_cli_missing_backend_in_record_mode():
    """Record mode requires --backend argument."""
    result = subprocess.run(
        [sys.executable, "-m", "capturly", "--mode", "record"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "backend" in result.stderr.lower()
