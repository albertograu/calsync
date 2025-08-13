#!/bin/bash
# Simple script to copy existing Google OAuth token

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}📋 Copy Existing Google OAuth Token${NC}"
echo ""
echo -e "${YELLOW}Use this if you already have a working OAuth token from:${NC}"
echo "• Your VPS deployment"
echo "• Another CalSync installation" 
echo "• Previous local setup"
echo ""

# Check if token already exists locally
if [ -f "./credentials/google_token.json" ]; then
    echo -e "${GREEN}✅ Token already exists in Docker volume${NC}"
    echo -e "${YELLOW}Current token:${NC}"
    ls -la ./credentials/google_token.json
    echo ""
    read -p "Replace existing token? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Keeping existing token${NC}"
        exit 0
    fi
fi

echo -e "${YELLOW}Token locations to check:${NC}"
echo "1. ~/.calsync-claude/credentials/google_token.json (local CalSync)"
echo "2. VPS: /path/to/calsync/credentials/google_token.json"
echo "3. VPS: /path/to/calsync/data/credentials/google_token.json"
echo ""

# Check common local locations
if [ -f ~/.calsync-claude/credentials/google_token.json ]; then
    echo -e "${GREEN}✅ Found local token: ~/.calsync-claude/credentials/google_token.json${NC}"
    read -p "Copy this token? (Y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo -e "${YELLOW}Skipping local token${NC}"
    else
        cp ~/.calsync-claude/credentials/google_token.json ./credentials/
        echo -e "${GREEN}✅ Token copied from local CalSync installation${NC}"
        exit 0
    fi
fi

# Manual copy instructions
echo -e "${YELLOW}Manual copy options:${NC}"
echo ""
echo -e "${BLUE}Option 1 - From VPS:${NC}"
echo "  scp your-vps:/path/to/calsync/credentials/google_token.json ./credentials/"
echo ""
echo -e "${BLUE}Option 2 - From local file:${NC}"
echo "  cp /path/to/your/google_token.json ./credentials/"
echo ""
echo -e "${BLUE}Option 3 - Use auth setup:${NC}"
echo "  ./dev.sh auth    # Full OAuth setup with browser"
echo ""

read -p "Enter path to existing token file (or press Enter to skip): " token_path

if [ -n "$token_path" ] && [ -f "$token_path" ]; then
    cp "$token_path" ./credentials/google_token.json
    echo -e "${GREEN}✅ Token copied successfully${NC}"
    
    # Test the token
    echo -e "${BLUE}🧪 Testing token with Docker...${NC}"
    ./dev.sh test
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}🎉 Token works! Your development environment is ready.${NC}"
    else
        echo -e "${RED}❌ Token test failed. You may need to refresh it.${NC}"
        echo -e "${YELLOW}Try: ./dev.sh auth${NC}"
    fi
elif [ -n "$token_path" ]; then
    echo -e "${RED}❌ File not found: $token_path${NC}"
    exit 1
else
    echo -e "${YELLOW}No token path provided. Use './dev.sh auth' for full setup.${NC}"
fi