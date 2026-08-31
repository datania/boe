.PHONY: setup test run upload clean help

help:
	@echo "Available targets:"
	@echo "  setup   - Install dependencies using uv"
	@echo "  test    - Run the test suite"
	@echo "  run     - Run the BOE downloader"
	@echo "  upload  - Upload downloaded files to Hugging Face"
	@echo "  clean   - Remove downloaded files"
	@echo "  help    - Show this help message"

.uv:
	@uv -V || echo 'Please install uv: https://docs.astral.sh/uv/getting-started/installation/'

setup: .uv
	uv sync

test: .uv
	uv run -m unittest discover -s tests -v

run: .uv
	uv run boe.py $(ARGS)

upload:
	@test -n "$${HF_TOKEN}" || (echo "HF_TOKEN is required" >&2; exit 1)
	uvx --from "huggingface_hub[hf_xet]" hf upload \
		datania/boe boe/ . \
		--repo-type dataset

clean:
	rm -rf boe/
