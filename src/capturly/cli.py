"""Command-line interface for Capturly."""

import argparse
import os

from . import config, server


def main(argv=None):
    """Parse command-line options and run the Capturly server."""
    parser = argparse.ArgumentParser(
        description="Record/replay mock server for performance testing"
    )
    parser.add_argument(
        "--mode",
        choices=["record", "replay", "hybrid", "log"],
        default="replay",
        help="Mode: record (proxy + cache), replay (saved responses), hybrid (cache-through), or log (proxy + full req/res logs)",
    )
    parser.add_argument(
        "--backend",
        type=str,
        help="Backend URL for RECORD/HYBRID/LOG modes (e.g., https://adoption-backend...)",
    )
    parser.add_argument(
        "--port", type=int, default=9999, help="Port to listen on (default: 9999)"
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=0,
        help="Delay in milliseconds before replayed responses in REPLAY and HYBRID cache-hit mode (default: 0)",
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--combine-chunks",
        action="store_true",
        help="In LOG mode, combine OpenAI-style SSE chunks into one final traffic log response",
    )
    parser.add_argument(
        "--recordings-dir",
        type=str,
        help="Directory for recordings (default: ./capturly-recordings or CAPTURLY_RECORDINGS_DIR)",
    )
    parser.add_argument(
        "--config",
        type=str,
        dest="config_file",
        help="Path to YAML config file (default: ./capturly.yaml or ~/.capturly/config.yaml)",
    )
    args = parser.parse_args(argv)

    # Load and merge config file (CLI args take priority)
    config_path = config.find_config_file(explicit_path=args.config_file)
    if config_path:
        cfg = config.load_config(config_path)
        if cfg:
            args = config.merge_config_with_args(cfg, args)

    if args.recordings_dir:
        os.environ["CAPTURLY_RECORDINGS_DIR"] = args.recordings_dir

    if args.mode in ("record", "hybrid", "log") and not args.backend:
        parser.error("--backend is required in RECORD, HYBRID, and LOG modes")
    if args.delay < 0:
        parser.error("--delay must be a non-negative integer")

    server.run_server(args)
