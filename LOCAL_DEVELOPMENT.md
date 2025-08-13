# 🐳 Local Development Setup

This guide helps you set up CalSync for local testing instead of deploying to VPS every time.

## 🚀 Quick Start

### 1. Initial Setup (First Time Only)
```bash
# Set up environment and directories
./dev.sh setup

# Edit credentials (copy from your VPS .env if you have one)
nano .env

# Set up Google OAuth authentication (opens browser)
./dev.sh auth
```

### 2. Start Testing
```bash
# Build and run in foreground with logs (recommended for testing)
./dev.sh run

# OR run in background
./dev.sh daemon
./dev.sh logs  # Follow logs
```

### 3. Test Your Changes
```bash
# Make code changes, then rebuild to test
./dev.sh rebuild

# Or just rebuild and run
./dev.sh rebuild
```

## 📋 Environment Setup

### Required Credentials
Add these to your `.env` file:

**Google Calendar API:**
- Get from: [Google Cloud Console](https://console.developers.google.com/)
- `GOOGLE_CLIENT_ID` - Your OAuth 2.0 client ID
- `GOOGLE_CLIENT_SECRET` - Your OAuth 2.0 client secret

**iCloud CalDAV:**
- `ICLOUD_USERNAME` - Your Apple ID email
- `ICLOUD_PASSWORD` - App-specific password (NOT your main password)
- Generate app-specific password: [Apple ID Settings](https://appleid.apple.com/account/manage)

### Google OAuth Setup
Google requires browser-based OAuth authentication. Run this once:

```bash
./dev.sh auth
```

This will:
1. Install CalSync locally (if needed)
2. Open your browser for Google OAuth
3. Copy the token to your Docker environment
4. Test the authentication

### Development Settings
The dev environment uses optimized settings for testing:
- **Sync Interval:** 3 minutes (vs 30 in production)
- **Debug Logging:** Enabled 
- **Smaller Batches:** 100 events max per sync
- **Shorter Timespan:** 7 days past, 30 days future

## 🛠️ Development Commands

### Core Commands
```bash
./dev.sh run       # Start sync daemon (foreground with logs)
./dev.sh daemon    # Start in background
./dev.sh logs      # Follow logs
./dev.sh stop      # Stop containers
./dev.sh rebuild   # Full rebuild after code changes
./dev.sh auth      # Set up/refresh Google OAuth
```

### Testing Commands
```bash
./dev.sh test      # One-time dry-run sync test
./dev.sh shell     # Open shell in container for debugging
./dev.sh status    # Show environment status
```

### Maintenance Commands  
```bash
./dev.sh clean     # Clean up containers/images
./dev.sh restart   # Quick restart
./dev.sh build     # Just build (no run)
```

## 🔍 Testing Your Changes

### Typical Development Workflow
1. **Make code changes** in your editor
2. **Rebuild and test:**
   ```bash
   ./dev.sh rebuild
   ```
3. **Watch logs** for your changes in action
4. **Iterate** until satisfied
5. **Commit** when ready

### Testing Specific Issues
```bash
# Test sync errors with detailed logging
./dev.sh run    # Watch logs in real-time

# Test dry-run without making changes
./dev.sh test   # One-time dry-run test

# Debug inside container
./dev.sh shell
# Then run: calsync-claude sync --dry-run --verbose
```

## 📁 File Structure

```
calsync-claude/
├── .env                    # Your local credentials (create from template)
├── .env.template          # Template for .env
├── docker-compose.dev.yml # Development compose file
├── dev.sh                 # Development helper script
├── data/                  # Local sync data and database
├── credentials/           # OAuth tokens (auto-created)
└── src/                   # Source code
```

## 🎯 Development vs Production

| Aspect | Development | Production |
|--------|-------------|------------|
| Sync Interval | 3 minutes | 30 minutes |
| Logging | DEBUG | INFO |
| Batch Size | 100 events | 1000 events |
| Time Range | 7 days past, 30 future | 30 days past, 365 future |
| Auto-restart | No | Yes |
| Container Name | `calsync-claude-dev` | `calsync-claude` |

## 🐛 Troubleshooting

### Container Won't Start
```bash
# Check environment and authentication status
./dev.sh status

# If OAuth token is missing, set it up
./dev.sh auth

# Check logs for errors
./dev.sh logs

# Clean rebuild
./dev.sh clean
./dev.sh rebuild
```

### Code Changes Not Taking Effect
```bash
# Force clean rebuild
./dev.sh rebuild
```

### Credential Issues
```bash
# Verify .env file
cat .env

# Check authentication status
./dev.sh status

# Refresh OAuth token
./dev.sh auth

# Test authentication manually
./dev.sh shell
calsync-claude auth test
```

### Database Issues
```bash
# Clear local database
rm -rf data/
./dev.sh restart
```

## 📝 Tips

1. **Use `./dev.sh run`** for active development - shows logs immediately
2. **Use `./dev.sh rebuild`** after code changes
3. **Check `./dev.sh status`** if something seems wrong
4. **Use `./dev.sh test`** for safe dry-run testing
5. **Run `./dev.sh auth` once** to set up Google OAuth
6. **Keep your `.env`** file secure and don't commit it
7. **Token expires?** Just run `./dev.sh auth` again

## 🔗 Related Commands

Once your local testing is working, you can still use your VPS commands:
- `./quick.sh` - For production deployment
- `./rebuild.sh` - For production rebuilds

The local dev environment is completely separate from your VPS setup.

---

**Happy developing! 🎉** Now you can test changes locally before deploying to your VPS.