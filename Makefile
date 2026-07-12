.PHONY: install test lint format hygiene check data slice tree clean

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

data:
	python scripts/download_dataset.py all --config configs/project.yaml

slice:
	python scripts/run_vertical_slice.py --config configs/project.yaml

tree:
	find . -maxdepth 4 -print

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage build dist *.egg-info
