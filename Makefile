.PHONY: install skill

install:
	@test -n "$(REPO)" || { echo "REPO=/abs/path/to/repo is required" >&2; exit 1; }
	python3 scripts/setup_main.py "$(REPO)" $(if $(LOCALE),--locale "$(LOCALE)")

skill:
	@./scripts/bootstrap.sh --quiet
