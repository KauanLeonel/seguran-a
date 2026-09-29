#!/bin/sh
set -eu
APP_PASSWORD=$(cat /run/secrets/db_app_password)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$APP_PASSWORD" <<'SQL'
CREATE ROLE campus_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE ALL ON DATABASE campus FROM PUBLIC;
GRANT CONNECT ON DATABASE campus TO campus_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO campus_app;
SQL
