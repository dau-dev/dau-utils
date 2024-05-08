tests: ## Make unit tests
	python -m pytest -v dau_utils --cov=dau_utils --junitxml=junit.xml --cov-report=xml --cov-branch
test: tests # alias

lint: ## run linter
	python -m isort --check dau_utils setup.py
	python -m ruff check dau_utils setup.py
	python -m ruff format --check dau_utils setup.py

fix:  ## run black fix
	python -m isort dau_utils/ setup.py
	python -m ruff format dau_utils/ setup.py

check:  ## run manifest checks
	check-manifest -v

clean: ## clean the repository
	find . -name "__pycache__" | xargs  rm -rf
	find . -name "*.pyc" | xargs rm -rf
	rm -rf .coverage cover htmlcov logs build dist *.egg-info

install:  ## install to site-packages
	python -m pip install .

dev:
	python -m pip install .[develop]
develop: dev # alias

dist:  ## create dists
	rm -rf dist build
	python setup.py sdist bdist_wheel
	python -m twine check dist/*

publish: dist  ## dist to pypi
	python -m twine upload dist/* --skip-existing

# Thanks to Francoise at marmelab.com for this
.DEFAULT_GOAL := help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

print-%:
	@echo '$*=$($*)'

.PHONY: tests test lint fix check clean install dev dist publish help
