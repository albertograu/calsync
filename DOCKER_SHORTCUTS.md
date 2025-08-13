# 🐳 Docker Shortcuts for CalSync

## 🚀 Local Development (NEW!)

**For testing changes locally without VPS deployment:**

```bash
# First-time setup
./dev.sh setup        # Create .env, directories
nano .env              # Add your credentials
./dev.sh auth          # Set up Google OAuth (opens browser)

# Development workflow  
./dev.sh run           # Start with live logs (recommended)
./test-changes.sh      # Quick test after making changes
./dev.sh rebuild       # Full rebuild after code changes

# Other dev commands
./dev.sh daemon        # Run in background
./dev.sh test          # One-time dry-run test
./dev.sh logs          # Follow logs
./dev.sh status        # Show environment status
```

📚 **See [LOCAL_DEVELOPMENT.md](./LOCAL_DEVELOPMENT.md) for full development guide**

## 🏭 Production/VPS Commands

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

### Development (Local Testing)
```bash
# Build dev environment
docker compose -f docker-compose.dev.yml build

# Start dev environment
docker compose -f docker-compose.dev.yml up

# Run test profile
docker compose -f docker-compose.dev.yml --profile test up calsync-test
```

### Production (VPS)
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

## 🔧 Troubleshooting

### Local Development Issues
If you're testing locally and having issues:
1. **Use `./dev.sh rebuild`** to force a clean rebuild
2. **Check `./dev.sh status`** to see what's wrong
3. **Use `./dev.sh clean`** to completely reset
4. **Check your `.env` file** has correct credentials

### Production/VPS Issues  
If you're seeing old code behavior after making changes:
1. **Use `./quick.sh rebuild`** to force a clean rebuild
2. This stops containers, rebuilds with `--no-cache`, and starts fresh
3. The `rebuild.sh` script is your friend when code changes aren't taking effect!

### Quick Debugging
```bash
# Local development
./dev.sh shell         # Open shell in dev container
./dev.sh logs          # Follow dev logs

# Production
docker exec -it calsync-claude /bin/bash  # Shell in production
./quick.sh logs        # Follow production logs
```

## 🎯 Which Commands to Use?

| Scenario | Commands |
|----------|----------|
| **Local testing of changes** | `./dev.sh run` or `./test-changes.sh` |
| **Deploy to VPS** | `./quick.sh rebuild` |
| **Quick local test** | `./dev.sh test` (dry-run) |
| **Debug locally** | `./dev.sh shell` |
| **Debug on VPS** | `docker exec -it calsync-claude /bin/bash` |

**💡 Tip: Test locally first, then deploy to VPS when satisfied!**