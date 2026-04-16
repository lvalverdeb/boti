.PHONY: help clean build upload upload-test install-dev test lint format typecheck coverage check

help:
	@echo "Available targets:"
	@echo "  clean        - Remove build and distribution artifacts"
	@echo "  build        - Build sdist and wheel"
	@echo "  upload       - Upload to PyPI (requires UV_PUBLISH_TOKEN or .netrc)"
	@echo "  upload-test  - Upload to TestPyPI (requires UV_PUBLISH_TOKEN or .netrc)"
	@echo "  install-dev  - Install package with dev dependencies"
	@echo "  test         - Run test suite"
	@echo "  lint         - Run ruff linter"
	@echo "  format       - Run ruff formatter"
	@echo "  typecheck    - Run mypy type checker"
	@echo "  coverage     - Run tests with coverage report"
	@echo "  check        - Validate package with twine check"

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info htmlcov/ .coverage

build: clean
	uv build

upload: build
	@if [ -f .env ]; then set -a; . .env; set +a; fi && uv publish

upload-test: build
	@if [ -f .env ]; then set -a; . .env; set +a; fi && uv publish --publish-url https://test.pypi.org/legacy/

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
	uv publish --check dist/*
