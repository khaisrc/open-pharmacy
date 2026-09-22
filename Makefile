.PHONY: run e2e units

# Bring up the full stack: db, migrate, web.
run:
	docker compose up --build


# Curl-driven black-box suite against a running server (see e2e/README.md).
e2e:
	docker compose up -d
	./e2e/run.sh

# Django test suite, run inside the Compose web container (see AGENTS.md).
units:
	docker compose run --rm web pytest
