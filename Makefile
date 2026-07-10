# Makefile

all: venv python pre-commit

clean:
	$(RM) -r venv

pre-commit:
	venv/bin/pre-commit install

python:
	venv/bin/python3 -m pip install --upgrade pip
	venv/bin/pip install -r requirements.txt pre-commit

venv:
	test -d venv || python3 -m venv venv
