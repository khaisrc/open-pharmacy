.PHONY: run reset e2e units lint format

# The identity server is behind Compose's `auth` profile, and these targets
# turn it on by default. NO_AUTH=1 leaves it out:
#
#   make run NO_AUTH=1
#
# (A `--no-auth` flag is not possible: make parses any leading-`--` argument
# as one of its own options and fails before reading the Makefile.) The API
# runs either way -- it fetches the provider's keys on the first request that
# presents a provider token, and `manage.py mint_token` needs no provider at
# all -- so dropping it is a faster stack, not a different one.
AUTH_PROFILE := $(if $(NO_AUTH),,--profile auth)

# Bring up the full stack: db, migrate, web, and the identity server.
run:
	docker compose $(AUTH_PROFILE) up --build

# Wipe the local database and bring the stack back up clean: fresh
# migrations and fresh seed data, no stale schema or demo rows left over.
# `down -v` drops the identity server's database too, so its console loses
# its admin and `auth-seed` creates it again on the way back up.
reset:
	docker compose down -v
	docker compose $(AUTH_PROFILE) up --build


# Curl-driven black-box suite against a running server (see e2e/README.md).
# CASE limits the run to case files whose name contains it: make e2e CASE=65_fills
#
# No `auth` profile, deliberately: the suite mints its own tokens and never
# calls the provider, so this is the same stack CI runs against.
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
