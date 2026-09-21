FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_ROOT=/opt/staticfiles \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies install in their own layer: editing source never rebuilds the venv.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --all-groups

COPY . .

RUN DJANGO_ENV=local python manage.py collectstatic --noinput --clear

RUN useradd --create-home --uid 1000 app && chown -R app:app /app /opt/venv /opt/staticfiles
USER app

EXPOSE 8000

# No entrypoint: the image never migrates on start. Migrations are a separate
# one-shot step (the `migrate` Compose service locally; a pre-deploy job in
# uat and prod), so N replicas starting together never race on the same DDL.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
