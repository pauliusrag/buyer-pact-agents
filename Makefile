.PHONY: install check-env check-env-live test test-live lint fmt api demo demo-offline demo-edge demo-scale market-research market-research-live replay clean

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

fmt:
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

api:
	uv run uvicorn sye.main:app --reload --port 8000

demo:
	uv run python scripts/run_demo.py examples/demo_easy.json --verbose

demo-offline:
	uv run python scripts/run_demo.py examples/users_monitors.json --offline --verbose

check-env:
	uv run python scripts/check_env.py

check-env-live:
	uv run python scripts/check_env.py --probe

market-research:
	uv run python scripts/run_market_research.py examples/users_named.json --verbose

market-research-live:
	uv run python scripts/run_market_research.py examples/users_named.json --live --verbose

test-live:
	uv run pytest -m live

demo-edge:
	uv run python scripts/run_demo.py examples/demo_edge_cases.json --offline --verbose

demo-scale:
	uv run python scripts/run_demo.py examples/demo_scale.json --offline --verbose

replay:
	uv run python scripts/run_demo.py --replay $(RUN)

clean:
	rm -rf data/demo_runs/* data/sye.db .pytest_cache .ruff_cache
	touch data/demo_runs/.gitkeep
