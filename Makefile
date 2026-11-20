.PHONY: test
test:
	bash -n install.sh
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .github/release -v
