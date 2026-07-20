# Contributing to Capturly

Thank you for your interest in contributing to Capturly!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/vikrant82/capturly.git
cd capturly

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Type check
mypy src/capturly
```

## Project Structure

```
src/capturly/
├── cli.py          # Command-line interface
├── server.py       # HTTP server setup
├── handler.py      # Request handler (delegates to modes)
├── proxy.py        # Backend request forwarding
├── storage.py      # Disk persistence
├── logger.py       # Async traffic logging
├── sse.py          # Server-Sent Events handling
├── utils.py        # Shared utilities
└── modes/          # Operating mode implementations
    ├── record.py
    ├── replay.py
    ├── hybrid.py
    └── log.py
```

## Guidelines

- Follow existing code style (ruff + black configured in pyproject.toml)
- Write tests for new functionality
- Keep the core proxy stdlib-only (no external dependencies for core functionality)
- Python 3.9+ compatibility required
- No TODO/FIXME comments in committed code

## Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Make your changes with tests
4. Ensure `pytest tests/ -v` passes
5. Ensure `ruff check src/ tests/` passes
6. Submit a pull request with a clear description

## Reporting Issues

Use GitHub Issues. Include:
- Capturly version (`pip show capturly`)
- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Expected vs actual behavior
