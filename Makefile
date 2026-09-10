# mbtok - common tasks
PYTHON ?= python3
PROJECT ?= .

.PHONY: help install dev test test-fast lint doctor scan boards make batch clean

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

install:         ## Install mbtok into the current environment
	$(PYTHON) -m pip install .

dev:             ## Install with test dependencies, in editable mode
	$(PYTHON) -m pip install -e ".[dev]"

test:            ## Run the full suite, including real ffmpeg renders
	$(PYTHON) -m pytest tests/

test-fast:       ## Run everything except the ffmpeg integration tests
	$(PYTHON) -m pytest tests/ --ignore=tests/test_integration.py

doctor:          ## Check that this machine can render
	$(PYTHON) -m mbtok --project $(PROJECT) doctor

scan:            ## Index the configured media folders
	$(PYTHON) -m mbtok --project $(PROJECT) scan

boards:          ## Show which moods this library supports
	$(PYTHON) -m mbtok --project $(PROJECT) boards

make-post:       ## Render a single post
	$(PYTHON) -m mbtok --project $(PROJECT) make

batch:           ## Render a week of posts
	$(PYTHON) -m mbtok --project $(PROJECT) batch --count 7

clean:           ## Remove caches and build artefacts
	rm -rf build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
