# List available recipes
[private]
default:
    @just --list

# --all-extras, not --extra dev: dev dependencies live in [dependency-groups]
# and are synced by default, while `cloudrun` is an extra that `just types`
# needs resolvable. Without it, ty reports unresolved-import on the
# `from google.cloud import run_v2` line in src/tina/executors/cloudrun.py.
# CI's install step calls this recipe (.github/workflows/ci.yml), so there is
# nothing to keep in sync by hand.

# Install all dependencies, including the cloudrun extra
setup:
    uv sync --all-extras

# Run all checks: format check, lint, type check, example check, tests
check: fmt-check lint types example-check test

# Check formatting without writing changes
fmt-check:
    uv run ruff format --check

# Lint
lint:
    uv run ruff check

# Type check
types:
    uv run ty check

# Assert the recording's config is still the published example
#
# The gif is evidence for examples/bug-triage/ only while the recording runs
# that track. Re-recording would catch a divergence too, but it needs asciinema
# and agg, which CI does not have; this needs only sh, sed, grep, diff and cp,
# so the honesty rule is checked on every PR.
example-check:
    ./demo/workdir.sh --check

# Run tests
test *args:
    uv run pytest {{ args }}

# Run tests with coverage
coverage *args:
    uv run pytest --cov=tina --cov-report=term-missing {{ args }}

# Auto-fix formatting and lint
fmt:
    uv run ruff format
    uv run ruff check --fix

# Build the package
build:
    uv build

# Remove build artifacts and caches
clean:
    rm -rf dist/ build/ .pytest_cache/ .ruff_cache/ .coverage
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
