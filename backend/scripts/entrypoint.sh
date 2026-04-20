#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Waiting for PostgreSQL to start..."

# We use netcat (nc) to check if the DB port is open
while ! nc -z db 5432; do
  sleep 0.5
done

echo "PostgreSQL started successfully."

# ONLY run migrations if the command being executed is python or gunicorn
# (This prevents Celery workers from trying to run migrations at the same time)
if [ "$1" = "python" ] || [ "$1" = "gunicorn" ]; then
    echo "Applying database migrations..."
    python manage.py migrate
fi

# Execute the command passed to the docker container
exec "$@"
