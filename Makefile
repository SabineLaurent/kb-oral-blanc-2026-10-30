# The .env of the main working tree, shared by its git worktrees.
ENV_FILE := $(dir $(shell git rev-parse --path-format=absolute --git-common-dir)).env

.PHONY: create-db collect import write-tops digest serve-data serve-ai openapi

create-db:
	uv run --env-file $(ENV_FILE) kb create-db

collect:
	uv run --env-file $(ENV_FILE) kb collect

import:
	uv run --env-file $(ENV_FILE) kb import

write-tops:
	uv run --env-file $(ENV_FILE) kb write-tops

digest:
	uv run --env-file $(ENV_FILE) kb digest

serve-data:
	uv run --env-file $(ENV_FILE) kb serve-data

serve-ai:
	uv run --env-file $(ENV_FILE) kb serve-ai

openapi:
	uv run --env-file $(ENV_FILE) kb openapi
