# Contributing to OpenTor MCP

Thanks for helping improve the project. OpenTor MCP is an early-stage side project, so small,
focused pull requests are much easier to review than broad rewrites.

## Before you start

- Search existing issues and pull requests.
- Open an issue before a large feature or public tool-surface change.
- Do not attach session files, cookies, crawl datasets, CAPTCHA images, or sensitive page content.
- Use the project only for lawful, authorized work.

Security vulnerabilities should follow [SECURITY.md](SECURITY.md), not the public issue tracker.

## Local setup

```bash
git clone https://github.com/Medamine-cheddadi/opentor-mcp.git
cd opentor-mcp
uv sync --locked --extra dev
```

The unit suite is intentionally network-free and does not require Tor or Playwright browser
binaries.

## Development workflow

1. Add or update a regression test first.
2. Make the smallest implementation change that satisfies the test.
3. Keep user input validation at MCP, browser, and filesystem boundaries.
4. Update README or security documentation when behavior changes.
5. Run the complete verification gate:

```bash
uv run ruff format --check src tests
uv run ruff check .
uv run pyright src
uv run pytest --cov=tor_mcp --cov-report=term-missing
uv run python -m build
uv run pip-audit
```

Branch coverage must remain at or above 80%.

## Pull requests

Use a clear title, explain the user-visible change, call out security implications, and include the
commands you ran. Avoid unrelated formatting or generated crawl artifacts. By contributing, you
agree that your work is released under the repository's MIT License.
