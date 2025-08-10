# 🐳 Docker Shortcuts for CalSync

## Quick Commands

```bash
# Quick operations
./quick.sh build      # Build the container
./quick.sh up         # Start containers  
./quick.sh down       # Stop containers
./quick.sh logs       # Follow logs
./quick.sh restart    # Restart containers
./quick.sh rebuild    # Full rebuild (recommended for code changes)

# Full rebuild (stops, rebuilds, starts, shows logs)
./rebuild.sh
```

## Manual Commands

```bash
# Build
docker compose -f docker-compose.secrets.yml build

# Start
docker compose -f docker-compose.secrets.yml up -d

# Stop  
docker compose -f docker-compose.secrets.yml down

# Logs
docker compose -f docker-compose.secrets.yml logs -f

# Force rebuild (clears cache)
docker compose -f docker-compose.secrets.yml build --no-cache
```

## Troubleshooting

If you're seeing old code behavior after making changes:
1. Use `./quick.sh rebuild` to force a clean rebuild
2. This stops containers, rebuilds with `--no-cache`, and starts fresh

The `rebuild.sh` script is your friend when code changes aren't taking effect!