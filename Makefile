# Thin Makefile for Linux/macOS/CI. Every target delegates to the matching
# poe task defined in pyproject.toml, so the real logic and the "not yet
# implemented" stubs live in one place. On Windows, run `uv run poe <task>`
# directly — see CLAUDE.md "Development environment".

TARGET ?= bigquery

.PHONY: setup bq-setup data data-clean views lookml lookml-publish lookml-check verify-iam corpus test eval demo

setup:
	uv run poe setup

bq-setup:
	uv run poe bq-setup

data:
	uv run poe data --target $(TARGET)

data-clean:
	uv run poe data-clean --target $(TARGET)

views:
	uv run poe views

lookml:
	uv run poe lookml

lookml-publish:
	uv run poe lookml-publish

lookml-check:
	uv run poe lookml-check

verify-iam:
	uv run poe verify-iam

corpus:
	uv run poe corpus

test:
	uv run poe test

eval:
	uv run poe eval

demo:
	uv run poe demo
