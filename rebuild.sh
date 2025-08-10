#!/bin/bash
# CalSync Docker Rebuild Script

echo "🔥 Stopping containers..."
docker compose -f docker-compose.secrets.yml down

echo "🧹 Removing old images..."
docker compose -f docker-compose.secrets.yml build --no-cache

echo "🚀 Starting containers..."
docker compose -f docker-compose.secrets.yml up -d

echo "📄 Following logs..."
docker compose -f docker-compose.secrets.yml logs -f