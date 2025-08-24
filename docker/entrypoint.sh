#!/usr/bin/env sh
set -e

# Ensure dirs
mkdir -p "${DATA_DIR:-/data}" "${CREDENTIALS_DIR:-/credentials}"

# Map env to expected config if needed
export DATA_DIR
export CREDENTIALS_DIR

# If command is 'daemon' or 'sync', call CLI; else exec
if [ "$1" = "daemon" ]; then
  exec calsync-claude daemon --interval "${SYNC_CONFIG__SYNC_INTERVAL_MINUTES:-30}"
elif [ "$1" = "sync" ]; then
  shift
  exec calsync-claude sync "$@"
elif [ "$1" = "serve" ]; then
  shift
  exec calsync-claude serve --host 0.0.0.0 --port "${PORT:-8080}"
else
  exec "$@"
fi


