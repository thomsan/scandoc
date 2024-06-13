.PHONY: build install publish clean

build: clean
	python -m venv venv
	python -m pip install build
	python -m build

install: build
	pip install --force-reinstall dist/*.whl

uninstall:
	pip uninstall -y scandoc

publish:
	twine upload dist/*

clean:
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	rm -rf venv

requirements:
	pip install -r requirements.txt
