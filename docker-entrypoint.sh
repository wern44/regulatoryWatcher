#!/bin/bash
set -e

CONFIG="${REGWATCH_CONFIG:-/app/config.yaml}"

# Fall back to example config if no config.yaml is mounted
if [ ! -f "$CONFIG" ]; then
    echo "No config.yaml found at $CONFIG — copying from config.example.yaml"
    cp /app/config.example.yaml "$CONFIG"
fi

export REGWATCH_CONFIG="$CONFIG"

# First-run: initialise the database and load seed catalog
DB_FILE=$(python -c "
import yaml, pathlib
cfg = yaml.safe_load(pathlib.Path('$CONFIG').read_text())
print(cfg['paths']['db_file'])
")

if [ ! -f "$DB_FILE" ]; then
    echo "First run detected — initialising database..."
    regwatch init-db
    regwatch seed
    echo "Database ready."
fi

echo "Starting RegWatch on port 8001..."
exec uvicorn regwatch.main:app \
    --host 0.0.0.0 \
    --port 8001 \
    --workers 1
