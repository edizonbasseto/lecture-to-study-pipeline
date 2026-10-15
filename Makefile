.PHONY: test lint run site clean hooks demo-video demo-register demo-preflight \
	demo-run demo-site-access demo-site demo-serve demo-check demo-harvest \
	demo-recovery demo-report

WHISPER_MODEL_PATH ?= engines/whisper.cpp/models/ggml-large-v3-turbo.bin
DEMO_LOCAL_ID ?= 925100
DEMO_CODEX_ID ?= 925101
ADAPTER ?= llama-cpp
LECTURE ?= $(DEMO_LOCAL_ID)
CONFIRM_REMOTE_EGRESS ?=
FROM_STEP ?= run_all
FLASHCARDS_MINIMUM_TOTAL_CARDS ?=
DEMO_VIDEO := workspace/demo/aula-demo.mp4
DEMO_SITE_ACCESS := workspace/demo/site-access
DEMO_REGISTRATION_ARGUMENTS := --discipline "Cirurgia veterinária (demonstração)" \
	--semester 2026.2 --targets-path config/targets.example.yaml \
	--schedule-path demo/site/academic-calendar.yaml --publish

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

demo-video:
	uv run python scripts/build_demo_lecture.py

# The local id is registered without --remote-llm-allowed on purpose: a run
# whose adapter variable is missing fails closed instead of reaching the
# remote provider (ADR 0045, ADR 0046).
demo-register:
	uv run python scripts/register_local_lecture.py $(DEMO_LOCAL_ID) $(DEMO_VIDEO) \
		--title "Aula sintética: biópsia excisional e margens fasciais (adaptador local)" \
		$(DEMO_REGISTRATION_ARGUMENTS)
	uv run python scripts/register_local_lecture.py $(DEMO_CODEX_ID) $(DEMO_VIDEO) \
		--title "Aula sintética: biópsia excisional e margens fasciais (adaptador Codex)" \
		$(DEMO_REGISTRATION_ARGUMENTS) --remote-llm-allowed

demo-preflight:
	uv run python scripts/demo_run.py --adapter $(ADAPTER) --lecture-id $(LECTURE) \
		--whisper-model-path $(WHISPER_MODEL_PATH) --preflight-only \
		$(if $(filter yes,$(CONFIRM_REMOTE_EGRESS)),--confirm-remote-egress)

demo-run:
	uv run python scripts/demo_run.py --adapter $(ADAPTER) --lecture-id $(LECTURE) \
		--whisper-model-path $(WHISPER_MODEL_PATH) --from-step $(FROM_STEP) \
		$(if $(FLASHCARDS_MINIMUM_TOTAL_CARDS),--flashcards-minimum-total-cards $(FLASHCARDS_MINIMUM_TOTAL_CARDS)) \
		$(if $(filter yes,$(CONFIRM_REMOTE_EGRESS)),--confirm-remote-egress)

demo-site-access:
	uv run python scripts/demo_site.py prepare-access

demo-site:
	uv run python scripts/demo_site.py generate

demo-serve:
	LECTURE_STUDY_SITE_CREDENTIALS=$(DEMO_SITE_ACCESS)/site-credentials.txt \
	LECTURE_STUDY_SITE_SESSION_KEY=$(DEMO_SITE_ACCESS)/site-session-key \
	uv run uvicorn lecture_study.site.app:app --host 127.0.0.1 --port 8443 \
		--ssl-keyfile $(DEMO_SITE_ACCESS)/key.pem --ssl-certfile $(DEMO_SITE_ACCESS)/cert.pem

demo-check:
	uv run python scripts/demo_site.py check --lecture-id $(LECTURE)

# Copies the run's measured artifacts before any recovery step can overwrite them.
demo-harvest:
	uv run python scripts/demo_results.py harvest --adapter $(ADAPTER) \
		--lecture-id $(LECTURE) --asr-ablation --whisper-model-path $(WHISPER_MODEL_PATH)

# Induces each blocked state on the local-adapter lecture and recovers it (ADR 0045).
demo-recovery:
	uv run python scripts/demo_blocked_recovery.py --lecture-id $(DEMO_LOCAL_ID) --scenario both

demo-report:
	uv run python scripts/demo_results.py report

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
