#!/bin/bash
# Google OAuth Setup for Local Development

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🔐 Google OAuth Setup for CalSync Local Development${NC}"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ .env file not found!${NC}"
    echo -e "${YELLOW}Please run './dev.sh setup' first${NC}"
    exit 1
fi

# Check if we have Google credentials in .env
if ! grep -q "GOOGLE_CLIENT_ID=" .env || ! grep -q "GOOGLE_CLIENT_SECRET=" .env; then
    echo -e "${RED}❌ Google credentials not found in .env${NC}"
    echo -e "${YELLOW}Please add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file${NC}"
    exit 1
fi

echo -e "${YELLOW}📋 This script will:${NC}"
echo "1. Install CalSync locally (if needed)"
echo "2. Run local OAuth authentication with browser"
echo "3. Copy the token to your Docker volume"
echo "4. Test the authentication"
echo ""

read -p "Continue? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 0
fi

echo -e "${BLUE}🔍 Checking if CalSync is installed locally...${NC}"

# Function to find the right pip command
find_pip() {
    if command -v pip &> /dev/null; then
        echo "pip"
    elif command -v pip3 &> /dev/null; then
        echo "pip3"
    elif command -v python3 -m pip &> /dev/null; then
        echo "python3 -m pip"
    elif command -v python -m pip &> /dev/null; then
        echo "python -m pip"
    else
        return 1
    fi
}

# Check if calsync-claude is installed locally
if ! command -v calsync-claude &> /dev/null; then
    echo -e "${YELLOW}📦 CalSync not found locally. Installing in editable mode...${NC}"
    
    # Find the right pip command
    PIP_CMD=$(find_pip)
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ No pip installation found${NC}"
        echo -e "${YELLOW}Please install Python and pip first${NC}"
        exit 1
    fi
    
    echo -e "${BLUE}Using: $PIP_CMD${NC}"
    
    # Install in development mode
    $PIP_CMD install -e .
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to install CalSync locally${NC}"
        echo -e "${YELLOW}Trying with --user flag...${NC}"
        $PIP_CMD install -e . --user
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}❌ Installation failed with --user flag too${NC}"
            echo -e "${YELLOW}You may need to install Python dependencies first:${NC}"
            echo -e "  brew install python"
            echo -e "  or"
            echo -e "  $PIP_CMD install --upgrade pip setuptools wheel"
            exit 1
        fi
    fi
    
    echo -e "${GREEN}✅ CalSync installed locally${NC}"
else
    echo -e "${GREEN}✅ CalSync already installed locally${NC}"
fi

echo ""
echo -e "${BLUE}🌐 Starting OAuth authentication...${NC}"
echo -e "${YELLOW}⚠️  A browser window will open for Google OAuth${NC}"
echo -e "${YELLOW}   Complete the authentication in your browser${NC}"
echo ""

# Set up environment variables from .env
echo -e "${BLUE}📝 Loading credentials from .env...${NC}"

# Export environment variables (filter out comments and empty lines)
while IFS= read -r line; do
    # Skip comments and empty lines
    [[ $line =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    
    # Export valid variable assignments
    if [[ $line =~ ^[A-Z_]+=.+ ]]; then
        export "$line"
        # Don't echo the values for security
        var_name=$(echo "$line" | cut -d'=' -f1)
        echo -e "  ✅ Loaded $var_name"
    fi
done < .env

# Verify required variables are set
if [[ -z "$GOOGLE_CLIENT_ID" || -z "$GOOGLE_CLIENT_SECRET" ]]; then
    echo -e "${RED}❌ Missing Google credentials in .env${NC}"
    echo -e "${YELLOW}Please ensure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set${NC}"
    exit 1
fi

# Create local config directory
mkdir -p ~/.calsync-claude/credentials

echo -e "${BLUE}🔑 Running OAuth authentication...${NC}"
echo -e "${YELLOW}Note: This will open your browser for Google OAuth${NC}"

# Run the authentication test which will trigger OAuth
calsync-claude test

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ OAuth authentication successful!${NC}"
else
    echo -e "${RED}❌ OAuth authentication failed${NC}"
    echo -e "${YELLOW}This could be due to:${NC}"
    echo -e "  1. Missing/incorrect Google credentials in .env"
    echo -e "  2. Google OAuth app not configured properly"
    echo -e "  3. Network/firewall issues"
    echo ""
    echo -e "${YELLOW}Alternative: Manual token setup${NC}"
    echo -e "If you have a working token from your VPS, you can copy it:"
    echo -e "  scp your-vps:/path/to/google_token.json ./credentials/"
    exit 1
fi

echo ""
echo -e "${BLUE}📁 Copying token to Docker volume...${NC}"

# Ensure Docker credentials directory exists
mkdir -p ./credentials

# Copy the token file
if [ -f ~/.calsync-claude/credentials/google_token.json ]; then
    cp ~/.calsync-claude/credentials/google_token.json ./credentials/
    echo -e "${GREEN}✅ Token copied to Docker volume${NC}"
else
    echo -e "${RED}❌ Token file not found${NC}"
    echo -e "${YELLOW}OAuth may have failed. Check the output above.${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}🧪 Testing Docker authentication...${NC}"

# Test with Docker
./dev.sh test

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}🎉 Setup complete! Your local development environment is ready.${NC}"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo -e "  • Start development sync: ${BLUE}./dev.sh run${NC}"
    echo -e "  • Test your changes: ${BLUE}./test-changes.sh${NC}"
    echo -e "  • Follow logs: ${BLUE}./dev.sh logs${NC}"
else
    echo -e "${RED}❌ Docker authentication test failed${NC}"
    echo -e "${YELLOW}Check the logs above for issues${NC}"
fi