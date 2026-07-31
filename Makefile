# boti is its own git repo nested inside the uv workspace root, so
# `git rev-parse --show-toplevel` would return boti's own root, not
# the workspace root where `uv build` actually writes dist/ — derive it
# from the Makefile's own location instead (always one level up).
REPO_ROOT := $(shell realpath $(dir $(realpath $(lastword $(MAKEFILE_LIST))))..)

.PHONY: help clean build publish publish-test upload upload-test install-dev test lint format format-check typecheck coverage verify check

LOAD_ENV = if [ -f .env ]; then set -a; . .env; set +a; fi
REQUIRE_PUBLISH_TOKEN = test -n "$$UV_PUBLISH_TOKEN" || { echo "UV_PUBLISH_TOKEN is required (set it in .env or the environment)."; exit 1; }

help:
	@echo "Available targets:"
	@echo "  clean        - Remove build and distribution artifacts"
	@echo "  build        - Build sdist and wheel"
	@echo "  publish      - Publish to PyPI (loads UV_PUBLISH_TOKEN from .env if present)"
	@echo "  publish-test - Publish to TestPyPI (loads UV_PUBLISH_TOKEN from .env if present)"
	@echo "  upload       - Alias for publish (kept for backward compatibility)"
	@echo "  upload-test  - Alias for publish-test (kept for backward compatibility)"
	@echo "  install-dev  - Install package with dev dependencies"
	@echo "  test         - Run test suite"
	@echo "  lint         - Run ruff linter"
	@echo "  format       - Run ruff formatter"
	@echo "  format-check - Check formatting without applying (matches CI)"
	@echo "  typecheck    - Run mypy type checker"
	@echo "  coverage     - Run tests with coverage report"
	@echo "  verify       - Run the exact checks CI runs: lint + format-check + typecheck + coverage-gated tests"
	@echo "  check        - verify + dry-run publish"

clean:
	rm -rf $(REPO_ROOT)/dist/ $(REPO_ROOT)/build/ src/*.egg-info htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

build: clean
	uv build

publish: verify build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --token "$$UV_PUBLISH_TOKEN" $$(ls $(REPO_ROOT)/dist/boti-*)

publish-test: verify build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --publish-url https://test.pypi.org/legacy/ --token "$$UV_PUBLISH_TOKEN" $$(ls $(REPO_ROOT)/dist/boti-*)

# Backward-compatible aliases -- publish/publish-test are canonical (match
# spaghetti/fenceline's naming); upload/upload-test kept so nothing already
# calling them by the old name breaks.
upload: publish
upload-test: publish-test

install-dev:
	uv sync --group dev

test:
	uv run pytest

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

format-check:
	uv run ruff format --check src/ tests/

typecheck:
	uv run mypy src/

coverage:
	uv run pytest --cov=boti --cov-report=term-missing --cov-report=html

# Mirrors .github/workflows/ci.yml exactly (test + lint + typecheck jobs),
# so a local publish can't happen without the same checks CI enforces.
verify: lint format-check typecheck
	uv run pytest --cov=boti --cov-report=term-missing --cov-fail-under=80

check: verify build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --dry-run --token "$$UV_PUBLISH_TOKEN" $$(ls $(REPO_ROOT)/dist/boti-*)
