#!/bin/bash
set -e

# Fix ownership of mounted volumes (bind mounts are root-owned)
chown -R appuser:appuser /app/cache

exec gosu appuser "$@"
