.PHONY: install test lint format hygiene check slice tree clean

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests scripts

hygiene:
	python scripts/check_repo_hygiene.py

format:
	ruff format src tests scripts

check: lint hygiene test

slice:
	python scripts/run_vertical_slice.py --config configs/project.yaml

tree:
	find . -maxdepth 4 -print

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage build dist *.egg-info
