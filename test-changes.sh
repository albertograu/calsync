#!/bin/bash
# Quick test script for testing code changes locally

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🧪 Testing Code Changes Locally${NC}"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  .env file missing. Run './dev.sh setup' first${NC}"
    exit 1
fi

echo -e "${YELLOW}📋 What this script does:${NC}"
echo "1. Stops any running containers"
echo "2. Rebuilds with your latest code changes" 
echo "3. Runs a quick dry-run test"
echo "4. Shows you the results"
echo ""

read -p "Continue? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 0
fi

echo -e "${BLUE}🔄 Stopping existing containers...${NC}"
./dev.sh stop

echo -e "${BLUE}🔨 Rebuilding with your changes...${NC}"
./dev.sh build

echo -e "${BLUE}🧪 Running test sync (dry-run)...${NC}"
echo -e "${YELLOW}This will test your changes without making actual calendar modifications${NC}"
echo ""

# Run the test and capture output
./dev.sh test

echo ""
echo -e "${GREEN}✅ Test complete!${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "• If the test looked good: './dev.sh run' to start live sync"
echo "• If you need to fix something: make changes and run this script again"
echo "• To start daemon in background: './dev.sh daemon'"
echo "• To see live logs: './dev.sh logs'"