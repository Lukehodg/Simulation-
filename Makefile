.PHONY: install init sync replay status test doctor clean-db

install:
	uv venv && uv pip install -e '.[dev]'

init:
	.venv/bin/health init

sync:
	.venv/bin/health sync

replay:
	.venv/bin/health replay

status:
	.venv/bin/health status

doctor:
	.venv/bin/health doctor

test:
	.venv/bin/pytest -q

# Rebuilds every derived table from raw/ without re-downloading anything.
clean-db:
	rm -f data/health.duckdb data/health.duckdb.wal
	.venv/bin/health init && .venv/bin/health replay
