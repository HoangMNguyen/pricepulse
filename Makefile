.PHONY: up down migrate test test-unit lint fmt serve build tf-plan tf-apply

up:
	docker compose up -d --wait db

down:
	docker compose down

migrate:
	uv run alembic upgrade head

test:
	uv run pytest -q

test-unit:
	uv run pytest -q -m 'not integration'

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff check --fix . && uv run ruff format .

serve:
	uv run uvicorn pricepulse.api.app:create_app --factory --reload --port 8000

build:
	scripts/build_lambda.sh

tf-plan:
	terraform -chdir=infra/envs/dev plan

tf-apply:
	terraform -chdir=infra/envs/dev apply
