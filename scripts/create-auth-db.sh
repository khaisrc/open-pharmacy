#!/bin/sh
# Runs once, when the db service initialises its volume: POSTGRES_DB creates
# open_pharmacy, and the identity server (the `auth` Compose service) keeps
# its own tables in a database of its own.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE DATABASE auth;
SQL
