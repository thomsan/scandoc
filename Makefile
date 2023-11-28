.PHONY: build install publish clean

build:
	python -m venv venv
	python -m build

install: build
	pip install --force-reinstall dist/*.whl

publish:
	twine upload dist/*

clean:
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	rm -rf venv
