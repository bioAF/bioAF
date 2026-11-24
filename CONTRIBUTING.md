# Contributing to bioAF

Thanks for your interest in contributing to bioAF! This document covers the process for contributing to the project.

## Getting Started

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Open a pull request against `main`

## Branch Protection

The `main` branch is protected. All changes must go through a pull request with:

- At least 1 approval from a code owner
- All CI checks passing
- Branch up to date with `main`

## Pull Request Process

1. Fill out the PR template completely
2. Ensure all CI checks pass
3. Wait for code owner review
4. Address any review feedback
5. Once approved, the code owner will merge

## CI Modes

The CI workflow runs on every pull request and picks its jobs from everything the pull request changes since it
branched from `main` (`scripts/ci/classify_changes.py`). The job summary of **Classify Changes** shows the mode and why.

| Mode | When | What runs |
| --- | --- | --- |
| docs | Only listed documentation: the root `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `GEMINI.md` and `RELEASE_NOTES.md`; Markdown under `docs/`, `decisions/` and `ai_guides/`; entries in `changes/unreleased/`; the PR template; the two architecture diagrams in `assets/` | Markdown lint, secret scan, dependency review |
| ci-config | Only `.github/dependabot.yml` or `.markdownlint.json`, with or without documentation | The above, plus workflow lint (actionlint, ShellCheck, zizmor) and the workflow regression tests |
| full | Anything else, including every file under `.github/workflows/` and any path the classifier does not know | Every job, with coverage gates |

To force full validation, add the `ci:full` label; removing it starts a new classified run. Label events trigger CI,
so adding or removing **any** label restarts the run in progress. **Build** keeps its own path filters and is not
affected by these modes.

## Development Setup

See the [README](README.md) for local development instructions.

## Code Style

- **Python (backend):** Formatted with `ruff format`, linted with `ruff check`, typed with `mypy`
- **TypeScript (frontend):** ESLint + Prettier, strict TypeScript
- **Terraform:** `terraform fmt`, `terraform validate`
- **Markdown:** Linted with markdownlint

## Architecture Decisions

Significant architectural decisions are documented as ADRs in the `decisions/` directory. If your contribution involves a meaningful architectural choice, please propose an ADR as part of your PR.

## Commit Messages

Use clear, descriptive commit messages. Prefix with the affected area when possible:

- `backend: add audit log middleware`
- `terraform: add SLURM spot instance toggle`
- `frontend: experiment registration form`
- `docs: update ADR-003 with IAP details`
- `ci: add Terraform validation step`

## Reporting Issues

Use the issue templates in this repository. Please include your bioAF version, the affected component, and steps to reproduce.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
