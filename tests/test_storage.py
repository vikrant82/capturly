import os
import tempfile

from capturly import storage


def test_get_recordings_dir_default():
    """Default recordings directory is ./capturly-recordings in current working directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        # Reset module-level constant
        storage.RECORDINGS_DIR = None
        result = storage.get_recordings_dir()
        assert result == os.path.join(os.path.realpath(tmpdir), "capturly-recordings")


def test_get_recordings_dir_from_env():
    """Environment variable overrides default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        custom_dir = os.path.join(tmpdir, "custom-recordings")
        os.environ["CAPTURLY_RECORDINGS_DIR"] = custom_dir
        storage.RECORDINGS_DIR = None
        result = storage.get_recordings_dir()
        assert result == custom_dir
        del os.environ["CAPTURLY_RECORDINGS_DIR"]


def test_get_recordings_dir_creates_directory():
    """Directory is created if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        storage.RECORDINGS_DIR = None
        result = storage.get_recordings_dir()
        assert os.path.exists(result)
