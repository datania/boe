.PHONY: setup run clean help

help:
	@echo "Available targets:"
	@echo "  setup  - Install dependencies using uv"
	@echo "  run    - Run the BOE downloader"
	@echo "  clean  - Remove downloaded files"
	@echo "  help   - Show this help message"

.uv:
	@uv -V || echo 'Please install uv: https://docs.astral.sh/uv/getting-started/installation/'

setup: .uv
	uv sync

run: .uv
	uv run boe.py

clean:
	rm -rf boe/
