#!/bin/sh
# Fix ownership of the pg-logs volume directory before Postgres starts.
# The named volume is created by Docker with root ownership; Postgres (UID 70
# on Alpine) needs write access so logging_collector can create the log file.
mkdir -p /var/log/postgresql
chown postgres:postgres /var/log/postgresql
exec /usr/local/bin/docker-entrypoint.sh "$@"
