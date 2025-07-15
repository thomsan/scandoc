.PHONY: build install publish clean

build: clean
	python -m venv .venv
	.venv/bin/pip install setuptools wheel build twine
	.venv/bin/python -m build

install: uninstall build
	/usr/bin/pip install --force-reinstall dist/*.whl

uninstall:
	/usr/bin/pip uninstall -y scandoc

publish: build
	twine upload dist/*

clean:
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	rm -rf .venv

requirements:
	pip install -r requirements.txt
