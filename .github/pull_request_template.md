## Summary

Describe the user-visible change and why it is needed.

## Security and compatibility

- Does this change handle URLs, selectors, files, cookies, web content, or credentials?
- Does it change a tool name, argument, annotation, or response shape?
- Are README, security notes, and the changelog still accurate?

## Verification

- [ ] Tests were added or updated first for behavior changes.
- [ ] `uv run ruff format --check src tests`
- [ ] `uv run ruff check .`
- [ ] `uv run pyright src`
- [ ] `uv run pytest --cov=tor_mcp --cov-report=term-missing`
- [ ] `uv run python -m build`
- [ ] `uv run pip-audit`
- [ ] No secrets, session files, screenshots, or crawl artifacts are included.
