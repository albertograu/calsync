#!/bin/bash
# Setup script for Docker secrets
# This script creates the secrets directory and prompts for credentials

set -e

SECRETS_DIR="./secrets"

echo "Setting up Docker secrets for CalSync Claude..."

# Create secrets directory with secure permissions
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

# Helper function to securely read credential
read_credential() {
    local name="$1"
    local description="$2"
    local file_path="$SECRETS_DIR/${name}.txt"
    
    echo -n "Enter $description: "
    read -s credential
    echo  # New line after hidden input
    
    echo -n "$credential" > "$file_path"
    chmod 600 "$file_path"
    echo "✓ Saved $description to $file_path"
}

# Prompt for each credential
echo
read_credential "google_client_id" "Google OAuth Client ID"
read_credential "google_client_secret" "Google OAuth Client Secret"
read_credential "icloud_username" "iCloud username/email"
read_credential "icloud_password" "iCloud app-specific password"

echo
echo "✅ Docker secrets setup complete!"
echo
echo "To use with Docker Compose:"
echo "  docker-compose -f docker-compose.secrets.yml up -d"
echo
echo "To clean up secrets (BE CAREFUL):"
echo "  rm -rf $SECRETS_DIR"
echo
echo "⚠️  Remember to add '$SECRETS_DIR' to your .gitignore file!"