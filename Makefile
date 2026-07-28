PYTHON ?= python
VENV_DIR := .venv
INSTALL_VENV_DIR := .scandoc-venv

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV_DIR)/Scripts/python.exe
INSTALL_VENV_PYTHON := $(INSTALL_VENV_DIR)/Scripts/python.exe
INSTALL_SCRIPTS_DIR := $(abspath $(INSTALL_VENV_DIR)/Scripts)
else
VENV_PYTHON := $(VENV_DIR)/bin/python
INSTALL_VENV_PYTHON := $(INSTALL_VENV_DIR)/bin/python
endif

.PHONY: build install uninstall publish clean requirements requirements-dev

build: clean
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	$(VENV_PYTHON) -m build

install: uninstall build
	$(PYTHON) -m venv $(INSTALL_VENV_DIR)
	$(PYTHON) -c "import glob, subprocess; wheels = glob.glob('dist/*.whl'); assert len(wheels) == 1, f'expected one wheel, found: {wheels}'; subprocess.check_call(['$(INSTALL_VENV_PYTHON)', '-m', 'pip', 'install', wheels[0]])"
ifeq ($(OS),Windows_NT)
	$(PYTHON) scripts/manage_user_path.py add "$(INSTALL_SCRIPTS_DIR)"
endif

uninstall: clean
ifeq ($(OS),Windows_NT)
	$(PYTHON) scripts/manage_user_path.py remove "$(INSTALL_SCRIPTS_DIR)"
endif
	$(PYTHON) -c "import shutil; shutil.rmtree('$(INSTALL_VENV_DIR)', ignore_errors=True)"

publish: build
	$(VENV_PYTHON) -m twine upload dist/*

clean:
	$(PYTHON) -c "import glob, shutil; [shutil.rmtree(path, ignore_errors=True) for pattern in ('dist', 'build', '*.egg-info', 'src/*.egg-info', '.venv') for path in glob.glob(pattern)]"

requirements:
	$(PYTHON) -m pip install -r requirements.txt

requirements-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt
