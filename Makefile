.PHONY: test lint run site clean hooks

WHISPER_MODEL_PATH ?= engines/whisper.cpp/models/ggml-large-v3-turbo.bin

test:
	uv run pytest --cov

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

run:
	uv run python -m lecture_study.cli.main run-all $(LECTURE) \
		--whisper-model-path $(WHISPER_MODEL_PATH)

site:
	uv run uvicorn lecture_study.site.app:app --reload --port 8000

clean:
	find . -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} +
	rm -rf .pytest_cache

HOOKS := pre-commit commit-msg pre-merge-commit pre-push

# Installs thin wrappers that always run the versioned hooks, so the
# installed copies never go stale; works in linked worktrees too.
hooks:
	@hooks_dir=$$(git rev-parse --git-path hooks); mkdir -p "$$hooks_dir"; \
	for hook in $(HOOKS); do \
		printf '#!/bin/sh\nexec "$$(git rev-parse --show-toplevel)/.githooks/%s" "$$@"\n' "$$hook" > "$$hooks_dir/$$hook"; \
		chmod 755 "$$hooks_dir/$$hook"; \
	done
