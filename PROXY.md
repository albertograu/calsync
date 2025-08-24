Reverse Proxy Examples
======================

Use these examples to route HTTPS traffic to the CalSync server container.

The server listens on PORT (default 8080) and exposes:
- POST /webhooks/google  (Google push notifications)
- GET  /health           (health endpoint)

Nginx
-----

server {
    listen 443 ssl http2;
    server_name your.domain;

    ssl_certificate     /etc/letsencrypt/live/your.domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your.domain/privkey.pem;

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8080/health;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    # Google webhook
    location /webhooks/google {
        proxy_pass http://127.0.0.1:8080/webhooks/google;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}

# Optional HTTP->HTTPS redirect
server {
    listen 80;
    server_name your.domain;
    return 301 https://$host$request_uri;
}

Caddy
-----

your.domain {
    encode gzip
    tls you@your.domain

    @google path /webhooks/google
    handle @google {
        reverse_proxy 127.0.0.1:8080
    }

    @health path /health
    handle @health {
        reverse_proxy 127.0.0.1:8080
    }

    # Default (optional)
    route {
        respond "OK" 200
    }
}

