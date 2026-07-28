PYTHON ?= python
VENV_DIR := .venv

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV_DIR)/Scripts/python.exe
else
VENV_PYTHON := $(VENV_DIR)/bin/python
endif

.PHONY: build install uninstall publish clean requirements requirements-dev

build: clean
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	$(VENV_PYTHON) -m build

install: uninstall build
	$(PYTHON) -c "import glob, subprocess, sys; wheels = glob.glob('dist/*.whl'); assert len(wheels) == 1, f'expected one wheel, found: {wheels}'; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--force-reinstall', wheels[0]])"

uninstall:
	$(PYTHON) -m pip uninstall -y scandoc

publish: build
	$(VENV_PYTHON) -m twine upload dist/*

clean:
	$(PYTHON) -c "import glob, shutil; [shutil.rmtree(path, ignore_errors=True) for pattern in ('dist', 'build', '*.egg-info', '.venv') for path in glob.glob(pattern)]"

requirements:
	$(PYTHON) -m pip install -r requirements.txt

requirements-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt
