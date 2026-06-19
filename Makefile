.PHONY: help clean build upload upload-test install-dev test lint format typecheck coverage check

LOAD_ENV = if [ -f .env ]; then set -a; . .env; set +a; fi
REQUIRE_PUBLISH_TOKEN = test -n "$$UV_PUBLISH_TOKEN" || { echo "UV_PUBLISH_TOKEN is required (set it in .env or the environment)."; exit 1; }

help:
	@echo "Available targets:"
	@echo "  clean        - Remove build and distribution artifacts"
	@echo "  build        - Build sdist and wheel"
	@echo "  upload       - Upload to PyPI (loads UV_PUBLISH_TOKEN from .env if present)"
	@echo "  upload-test  - Upload to TestPyPI (loads UV_PUBLISH_TOKEN from .env if present)"
	@echo "  install-dev  - Install package with dev dependencies"
	@echo "  test         - Run test suite"
	@echo "  lint         - Run ruff linter"
	@echo "  format       - Run ruff formatter"
	@echo "  typecheck    - Run mypy type checker"
	@echo "  coverage     - Run tests with coverage report"
	@echo "  check        - Dry-run publish to validate the package"

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info htmlcov/ .coverage

build: clean
	uv build

upload: build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --token "$$UV_PUBLISH_TOKEN" dist/*

upload-test: build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --publish-url https://test.pypi.org/legacy/ --token "$$UV_PUBLISH_TOKEN" dist/*

install-dev:
	uv sync --group dev

test:
	uv run pytest

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src/

coverage:
	uv run pytest --cov=boti --cov-report=term-missing --cov-report=html

check: build
	uv publish --dry-run dist/*
