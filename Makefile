.PHONY: run reset e2e units lint format

# Bring up the full stack: db, migrate, web.
run:
	docker compose up --build

# Wipe the local database and bring the stack back up clean: fresh
# migrations and fresh seed data, no stale schema or demo rows left over.
reset:
	docker compose down -v
	docker compose up --build


# Curl-driven black-box suite against a running server (see e2e/README.md).
# CASE limits the run to case files whose name contains it: make e2e CASE=65_fills
e2e:
	docker compose up -d
	./e2e/run.sh $(CASE)

# Django test suite, run inside the Compose web container (see AGENTS.md).
units:
	docker compose run --rm web pytest

# ruff check and format, the other two things CI runs (see AGENTS.md).
lint:
	docker compose run --rm web ruff check .
	docker compose run --rm web ruff format --check .

# Apply ruff's fixes and formatting in place, rather than just checking them.
format:
	docker compose run --rm web ruff check --fix .
	docker compose run --rm web ruff format .
