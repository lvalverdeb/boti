# boti is its own git repo nested inside the uv workspace root, so
# `git rev-parse --show-toplevel` would return boti's own root, not
# the workspace root where `uv build` actually writes dist/ — derive it
# from the Makefile's own location instead (always one level up).
REPO_ROOT := $(shell realpath $(dir $(realpath $(lastword $(MAKEFILE_LIST))))..)

.PHONY: help clean build breakglass-guard publish-breakglass publish-test upload-test install-dev test lint format format-check typecheck coverage verify check upgrade-check

LOAD_ENV = if [ -f .env ]; then set -a; . .env; set +a; fi
REQUIRE_PUBLISH_TOKEN = test -n "$$UV_PUBLISH_TOKEN" || { echo "UV_PUBLISH_TOKEN is required (set it in .env or the environment)."; exit 1; }

help:
	@echo "Available targets:"
	@echo "  clean        - Remove build and distribution artifacts"
	@echo "  build        - Build sdist and wheel"
	@echo "  publish-breakglass - Token publish to PyPI; ONLY when trusted publishing is down (needs BOTI_BREAKGLASS=1, see RELEASING.md)"
	@echo "  publish-test - Publish to TestPyPI (loads UV_PUBLISH_TOKEN from .env if present)"
	@echo "  upload-test  - Alias for publish-test (kept for backward compatibility)"
	@echo "  upgrade-check - Show what 'uv lock --upgrade' would change (dry run, standalone copy)"
	@echo "  install-dev  - Install package with dev dependencies"
	@echo "  test         - Run test suite (Hypothesis 'dev' profile: 1000 examples, local example DB)"
	@echo "  lint         - Run ruff linter"
	@echo "  format       - Run ruff formatter"
	@echo "  format-check - Check formatting without applying (matches CI)"
	@echo "  typecheck    - Run mypy type checker"
	@echo "  coverage     - Run tests with coverage report"
	@echo "  verify       - Run the exact checks CI runs: lint + format-check + typecheck + coverage-gated tests (Hypothesis 'ci' profile)"
	@echo "  check        - verify + dry-run publish"

clean:
	rm -rf $(REPO_ROOT)/dist/ $(REPO_ROOT)/build/ src/*.egg-info htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

build: clean
	uv build

# Releases are published by .github/workflows/publish.yml via PyPI trusted
# publishing, with attestations. This target is the break-glass fallback for
# when GitHub or PyPI OIDC is down: it needs an explicit BOTI_BREAKGLASS=1 on
# top of the token, and RELEASING.md says when it is appropriate.
breakglass-guard:
	@test "$$BOTI_BREAKGLASS" = "1" || { \
		echo "!! Break-glass publish refused."; \
		echo "!! The release path is: tag vX.Y.Z -> GitHub release -> publish.yml (trusted publishing + attestations)."; \
		echo "!! Only if that path is unavailable: BOTI_BREAKGLASS=1 make publish-breakglass  (then rotate the token; see RELEASING.md)"; \
		exit 1; }

publish-breakglass: breakglass-guard verify build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --token "$$UV_PUBLISH_TOKEN" $$(ls $(REPO_ROOT)/dist/boti-*)

publish-test: verify build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --publish-url https://test.pypi.org/legacy/ --token "$$UV_PUBLISH_TOKEN" $$(ls $(REPO_ROOT)/dist/boti-*)

# Backward-compatible alias (publish-test is canonical, matching
# spaghetti/fenceline's naming). `upload` is gone with `publish`.
upload-test: publish-test

# boti's uv.lock is resolved standalone (the workspace lock is the parent's),
# so the dry run happens in a clean export of HEAD, never in the checkout.
upgrade-check:
	@tmp=$$(mktemp -d) && git archive HEAD | tar -x -C "$$tmp" && cd "$$tmp" && uv lock --upgrade --dry-run; rm -rf "$$tmp"

install-dev:
	uv sync --group dev

test:
	HYPOTHESIS_PROFILE=dev uv run pytest

lint:
	uv run ruff check src/ tests/ examples/

format:
	uv run ruff format src/ tests/ examples/

format-check:
	uv run ruff format --check src/ tests/ examples/

typecheck:
	uv run mypy src/

coverage:
	uv run pytest --cov=boti --cov-report=term-missing --cov-report=html

# Mirrors .github/workflows/ci.yml exactly (test + lint + typecheck jobs),
# so a local publish can't happen without the same checks CI enforces.
verify: lint format-check typecheck
	HYPOTHESIS_PROFILE=ci uv run pytest --cov=boti --cov-report=term-missing --cov-fail-under=80

check: verify build
	@$(LOAD_ENV); $(REQUIRE_PUBLISH_TOKEN); uv publish --dry-run --token "$$UV_PUBLISH_TOKEN" $$(ls $(REPO_ROOT)/dist/boti-*)
