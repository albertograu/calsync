CalSync Claude – Container Deployment Guide
==========================================

Quick VPS deployment using Docker Compose with strict ID mapping and webhook server.

Requirements
- Docker + Compose v2
- Public HTTPS domain if enabling Google push webhooks

Setup
1) Clone repo and prepare env
   - cp .env.template .env
   - Edit .env with GOOGLE_CLIENT_ID/SECRET and iCloud credentials
   - mkdir -p data credentials
   - Copy Google OAuth token into credentials/google_token.json

2) Start the server
   - docker compose -f docker-compose.prod.yml build
   - docker compose -f docker-compose.prod.yml up -d
   - Health: curl http://localhost:${PORT:-8080}/health

3) Backfill strict IDs (one-time)
   - docker compose -f docker-compose.prod.yml exec calsync calsync-claude ids backfill

4) Enable Google push (optional)
   - Ensure reverse proxy routes https://your.domain/webhooks/google to the container port
   - Set GOOGLE_CHANNEL_TOKEN in .env
   - Register watch (server auto-renews in background):
     docker compose -f docker-compose.prod.yml exec calsync \
       calsync-claude google watch \
         --calendar "Personal" \
         --address "https://your.domain/webhooks/google" \
         --token "$GOOGLE_CHANNEL_TOKEN"

5) Renew watches
   - The server renews channels automatically every ${GOOGLE_CHANNEL_RENEW_INTERVAL_MINS:-60} minutes
     for channels expiring within ${GOOGLE_CHANNEL_RENEW_BEFORE_MINS:-1440} minutes.
   - Manual renew (optional):
     docker compose -f docker-compose.prod.yml exec calsync \
       calsync-claude google renew --renew-before-mins 1440 --token "$GOOGLE_CHANNEL_TOKEN"
   - Unwatch (optional):
     docker compose -f docker-compose.prod.yml exec calsync \
       calsync-claude google unwatch --calendar "Personal"

Reverse Proxy Notes
- Terminate TLS at proxy; route /webhooks/google and /health to container PORT (default 8080)
- Only HTTPS; server validates X-Goog-Channel-Token
- See PROXY.md for Nginx and Caddy examples

Operations
- Logs: docker compose -f docker-compose.prod.yml logs -f
- Update: git pull && docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d
- Health: GET /health returns last sync and poll interval
