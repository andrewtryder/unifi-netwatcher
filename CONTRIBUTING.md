# Contributing

Thanks for contributing to NetWatcher.

## Pull requests

This repository uses **squash merges**. The **PR title** becomes the commit on `main` and drives [Release Please](https://github.com/googleapis/release-please) versioning.

Use a [Conventional Commits](https://www.conventionalcommits.org/) title:

| Prefix | Effect |
|---|---|
| `feat:` | Minor release |
| `fix:` | Patch release |
| `feat!:` / `BREAKING CHANGE:` | Major release |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, … | Changelog / no version bump (unless configured) |

Examples: `feat: add webhook retries`, `fix: handle empty UniFi client list`.

## Local development

Requires [uv](https://docs.astral.sh/uv/), Node 20+, and optionally Docker.

```bash
git clone https://github.com/andrewtryder/unifi-netwatcher.git
cd unifi-netwatcher
cp .env.example .env

uv sync
npm ci && npm run build:css

# optional: install git hooks
uv run pre-commit install

uv run uvicorn app.main:app --reload --port 8080
```

For UI work without a UniFi controller, set `UNIFI_MOCK_MODE=true` in `.env`.

Docker-based development:

```bash
docker compose -f compose.yml -f compose.dev.yml up --build
```

## Checks before opening a PR

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest
npm ci && npm run build:css
```

CI also builds the Docker image and runs `actionlint` / `zizmor` on workflows.

## Releases

Merging conventional commits to `main` updates a Release Please PR. Merging that Release PR cuts a GitHub Release and publishes container images to GHCR and Docker Hub.

## Maintainer notes

### Required status checks (branch protection on `main`)

- `python`
- `frontend`
- `docker`
- `actions-lint`
- `Validate PR title`

After this CI lands on `main`, update branch protection to require those check names (replacing the legacy `test` check).

### CodeQL

Enable **CodeQL default setup** in the repository Security settings (Code scanning). Prefer GitHub’s default setup for Python and Actions rather than a custom workflow.
