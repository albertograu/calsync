# 🚀 CalSync Quick Start Guide

## 🏠 Local Development (Test Changes Fast!)

**One-time setup:**
```bash
./dev.sh setup    # Create .env and directories
nano .env          # Add Google/iCloud credentials
./dev.sh auth      # Set up OAuth (opens browser) ⚠️ Required!
```

**Daily development:**
```bash
./dev.sh run           # Start with live logs
# Make code changes...
./test-changes.sh       # Quick test your changes
./dev.sh rebuild        # Full rebuild when satisfied
```

**Status & debugging:**
```bash
./dev.sh status         # Check environment health
./dev.sh logs           # Follow container logs
./dev.sh shell          # Debug inside container
```

---

## 🏭 Production/VPS (Deploy to Server)

**Deploy changes:**
```bash
./quick.sh rebuild      # Full rebuild & deploy
./quick.sh logs         # Follow server logs
```

**Quick management:**
```bash
./quick.sh up/down      # Start/stop containers
./quick.sh restart      # Quick restart
```

---

## 🎯 Common Workflows

| What you want to do | Command |
|---------------------|---------|
| **Test code changes locally** | `./test-changes.sh` |
| **Start local development** | `./dev.sh run` |
| **Deploy to VPS** | `./quick.sh rebuild` |
| **Check if everything is set up** | `./dev.sh status` |
| **OAuth not working?** | `./dev.sh auth` |
| **Something broken?** | `./dev.sh clean && ./dev.sh rebuild` |

---

## 🆘 Troubleshooting

**"OAuth token missing"**
```bash
./dev.sh auth         # Set up browser authentication
# OR if you have an existing token:
./dev.sh copy-token   # Copy from VPS/other source
```

**"Container won't start"**
```bash
./dev.sh status  # Check what's missing
./dev.sh clean   # Clean up and retry
```

**"Code changes not working"**
```bash
./dev.sh rebuild # Force clean rebuild
```

---

## 💡 Pro Tips

- **Test locally first:** Use `./dev.sh run` before deploying to VPS
- **Quick testing:** `./test-changes.sh` for rapid iteration  
- **Debug mode:** Use `./dev.sh run` to see live logs
- **Clean slate:** `./dev.sh clean` if things get weird
- **Check status:** `./dev.sh status` shows what's missing

**📚 Full documentation:** See `LOCAL_DEVELOPMENT.md` for complete guide