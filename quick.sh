#!/bin/bash
# CalSync Quick Operations Script

case "$1" in
    "build")
        echo "🔨 Building CalSync..."
        docker compose -f docker-compose.secrets.yml build
        ;;
    "up")
        echo "🚀 Starting CalSync..."
        docker compose -f docker-compose.secrets.yml up -d
        ;;
    "down")
        echo "🔥 Stopping CalSync..."
        docker compose -f docker-compose.secrets.yml down
        ;;
    "logs")
        echo "📄 Following logs..."
        docker compose -f docker-compose.secrets.yml logs -f
        ;;
    "restart")
        echo "🔄 Restarting CalSync..."
        docker compose -f docker-compose.secrets.yml restart
        ;;
    "rebuild")
        echo "🏗️ Full rebuild..."
        ./rebuild.sh
        ;;
    *)
        echo "Usage: ./quick.sh {build|up|down|logs|restart|rebuild}"
        echo ""
        echo "Commands:"
        echo "  build   - Build the container"
        echo "  up      - Start containers"
        echo "  down    - Stop containers" 
        echo "  logs    - Follow logs"
        echo "  restart - Restart containers"
        echo "  rebuild - Full rebuild (down, build --no-cache, up, logs)"
        exit 1
        ;;
esac