#!/bin/bash
# CalSync Development Helper Script

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if .env file exists
check_env() {
    if [ ! -f ".env" ]; then
        echo -e "${RED}❌ .env file not found!${NC}"
        echo -e "${YELLOW}📋 Please copy .env.template to .env and fill in your credentials:${NC}"
        echo -e "   cp .env.template .env"
        echo -e "   nano .env"
        return 1
    fi
    return 0
}

# Ensure directories exist
setup_dirs() {
    echo -e "${BLUE}📁 Setting up directories...${NC}"
    mkdir -p data credentials
    chmod 755 data credentials
}

# Check if Google OAuth token exists
check_auth() {
    if [ ! -f "./credentials/google_token.json" ]; then
        echo -e "${RED}❌ Google OAuth token not found!${NC}"
        echo -e "${YELLOW}🔐 Please set up authentication first:${NC}"
        echo -e "   ./dev.sh auth"
        return 1
    fi
    return 0
}

case "$1" in
    "setup")
        echo -e "${BLUE}🔧 Setting up local development environment...${NC}"
        
        if [ ! -f ".env" ]; then
            if [ -f ".env.template" ]; then
                cp .env.template .env
                echo -e "${GREEN}✅ Created .env from template${NC}"
                echo -e "${YELLOW}📝 Please edit .env with your credentials:${NC}"
                echo -e "   nano .env"
            else
                echo -e "${RED}❌ .env.template not found${NC}"
                exit 1
            fi
        else
            echo -e "${GREEN}✅ .env already exists${NC}"
        fi
        
        setup_dirs
        
        echo -e "${GREEN}🎉 Setup complete!${NC}"
        echo -e "${YELLOW}Next steps:${NC}"
        echo -e "  1. Edit .env with your credentials: ${BLUE}nano .env${NC}"
        echo -e "  2. Set up Google OAuth: ${BLUE}./auth-setup.sh${NC}"
        echo -e "  3. Build and start: ${BLUE}./dev.sh run${NC}"
        ;;
        
    "auth")
        echo -e "${BLUE}🔐 Setting up Google OAuth authentication...${NC}"
        ./auth-setup.sh
        ;;
        
    "copy-token")
        echo -e "${BLUE}📋 Copying existing OAuth token...${NC}"
        ./copy-token.sh
        ;;
    
    "build")
        check_env || exit 1
        setup_dirs
        echo -e "${BLUE}🔨 Building development container...${NC}"
        docker compose -f docker-compose.dev.yml build
        ;;
        
    "run")
        check_env || exit 1
        check_auth || exit 1
        setup_dirs
        echo -e "${BLUE}🚀 Starting development sync (3-minute intervals)...${NC}"
        docker compose -f docker-compose.dev.yml up
        ;;
        
    "daemon")
        check_env || exit 1
        check_auth || exit 1
        setup_dirs
        echo -e "${BLUE}🚀 Starting development daemon in background...${NC}"
        docker compose -f docker-compose.dev.yml up -d
        echo -e "${GREEN}✅ Started! Use './dev.sh logs' to follow output${NC}"
        ;;
        
    "test")
        check_env || exit 1
        check_auth || exit 1
        setup_dirs
        echo -e "${BLUE}🧪 Running one-time sync test (dry-run)...${NC}"
        docker compose -f docker-compose.dev.yml --profile test up calsync-test
        ;;
        
    "logs")
        echo -e "${BLUE}📄 Following development logs...${NC}"
        docker compose -f docker-compose.dev.yml logs -f
        ;;
        
    "stop")
        echo -e "${BLUE}🔥 Stopping development containers...${NC}"
        docker compose -f docker-compose.dev.yml down
        ;;
        
    "restart")
        echo -e "${BLUE}🔄 Restarting development containers...${NC}"
        docker compose -f docker-compose.dev.yml restart
        ;;
        
    "rebuild")
        check_env || exit 1
        check_auth || exit 1
        setup_dirs
        echo -e "${BLUE}🏗️ Full development rebuild...${NC}"
        docker compose -f docker-compose.dev.yml down
        docker compose -f docker-compose.dev.yml build --no-cache
        docker compose -f docker-compose.dev.yml up
        ;;
        
    "clean")
        echo -e "${BLUE}🧹 Cleaning up development containers and images...${NC}"
        docker compose -f docker-compose.dev.yml down --volumes --remove-orphans
        docker image rm calsync-claude-calsync-dev 2>/dev/null || true
        echo -e "${GREEN}✅ Cleanup complete${NC}"
        ;;
        
    "shell")
        echo -e "${BLUE}🐚 Opening shell in development container...${NC}"
        docker compose -f docker-compose.dev.yml exec calsync-dev /bin/bash
        ;;
        
    "status")
        echo -e "${BLUE}📊 Development environment status:${NC}"
        echo -e "\n${YELLOW}Containers:${NC}"
        docker compose -f docker-compose.dev.yml ps
        echo -e "\n${YELLOW}Environment file:${NC}"
        if [ -f ".env" ]; then
            echo -e "${GREEN}✅ .env exists${NC}"
        else
            echo -e "${RED}❌ .env missing${NC}"
        fi
        echo -e "\n${YELLOW}Authentication:${NC}"
        if [ -f "./credentials/google_token.json" ]; then
            echo -e "${GREEN}✅ Google OAuth token exists${NC}"
        else
            echo -e "${RED}❌ Google OAuth token missing${NC}"
        fi
        echo -e "\n${YELLOW}Data directories:${NC}"
        ls -la data/ credentials/ 2>/dev/null || echo -e "${RED}❌ Directories missing${NC}"
        ;;
        
    *)
        echo -e "${BLUE}🔧 CalSync Development Helper${NC}"
        echo ""
        echo -e "${YELLOW}Setup Commands:${NC}"
        echo "  setup       - Initial setup (create .env, directories)"
        echo "  auth        - Set up Google OAuth authentication"
        echo "  copy-token  - Copy existing OAuth token from another source"
        echo ""
        echo -e "${YELLOW}Development Commands:${NC}"
        echo "  build     - Build development container"
        echo "  run       - Start sync daemon (foreground, 3min intervals)"
        echo "  daemon    - Start sync daemon (background)"
        echo "  test      - Run one-time dry-run test"
        echo "  logs      - Follow container logs"
        echo ""
        echo -e "${YELLOW}Management Commands:${NC}"
        echo "  stop      - Stop containers"
        echo "  restart   - Restart containers"
        echo "  rebuild   - Full rebuild and restart"
        echo "  clean     - Remove containers and images"
        echo "  shell     - Open shell in container"
        echo "  status    - Show environment status"
        echo ""
        echo -e "${YELLOW}Quick Start:${NC}"
        echo -e "  ${BLUE}./dev.sh setup${NC}    # First time only"
        echo -e "  ${BLUE}nano .env${NC}         # Add your credentials"
        echo -e "  ${BLUE}./dev.sh auth${NC}     # Set up Google OAuth (required)"
        echo -e "  ${BLUE}./dev.sh run${NC}      # Start testing"
        ;;
esac