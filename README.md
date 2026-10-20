# pyuchan

A lightweight, modern, and authentic imageboard engine written in Python 3 and Django. Built to serve as a high-performance, modular successor to classic imageboard scripts like Wakaba and Wakarimasen.

---

## Table of Contents

1. [Features](#features)
2. [Prerequisites](#prerequisites)
3. [Local Development Setup](#local-development-setup)
4. [Production Deployment Guide (Debian / Ubuntu)](#production-deployment-guide-debian--ubuntu)
   - [1. System Packages & User Setup](#1-system-packages--user-setup)
   - [2. Cloning & Virtual Environment](#2-cloning--virtual-environment)
   - [3. Configuration & Secrets](#3-configuration--secrets)
   - [4. Database Migration & Static Files](#4-database-migration--static-files)
   - [5. Systemd Service Units](#5-systemd-service-units)
   - [6. Nginx Web Server Configuration](#6-nginx-web-server-configuration)
   - [7. Periodic Maintenance & Cron Jobs](#7-periodic-maintenance--cron-jobs)
5. [Wakarimasen Migration](#wakarimasen-migration)
6. [License](#license)

---

## Features

- **Classic Styling:** Faithful Yotsuba (Futaba) and Yotsuba B (Burichan) colour schemes with instant, cookie-persisted client-side theme switching.
- **Asynchronous Media Pipeline:** Low-latency response times with background media handling powered by Huey. Generates lightweight WebP thumbnails and extracts video dimensions using Pillow and FFmpeg.
- **Multi-Format Upload Support:** Supports images (`.jpg`, `.png`, `.gif`, `.webp`), videos (`.webm`, `.mp4`), audio (`.mp3`, `.ogg`, `.flac`, `.wav`), and archives (`.zip`, `.7z`, `.tar.gz`) with custom filetype iconography.
- **Security & Anti-Abuse:**
  - Distorted, unpadded procedural CAPTCHA backed by atomic, one-time cache tokens.
  - Hardened IP extraction relying strictly on `REMOTE_ADDR` to prevent `X-Forwarded-For` spoofing.
  - Active Tor exit node detection (via Tor DNSEL) and multi-provider DNSBL query checking.
  - Synchronous image header validation (`Image.verify()`) to prevent polyglots and HTML-in-image attacks.
  - Decompression bomb mitigation capping image processing at 64 megapixels.
  - Protocol-sandboxed FFmpeg invocation (`-protocol_whitelist file`) with execution timeouts.
  - Deletion password brute-force limiting and CPU exhaustion protection against PBKDF2 flooding.
  - Honeypot form inputs to trap automated spam bots.
- **Modern Usability:**
  - Draggable, persistent Quick Reply (QR) window with selection quoting.
  - Inline one-click image and video expansion without breaking table layouts.
  - Reverse backlinks (`>>123`) with asynchronous hover previews for local and cross-board posts.
  - Live thread auto-updater with countdown timer and optional desktop notifications.
  - Expandable catalog view with search filters and size toggles.
- **Staff Controls:**
  - Authenticated capcodes (`## Admin` / `## Mod`).
  - Thread moving between boards with automatic post number and quote remapping.
  - Custom duration IP bans (hours, days, weeks, or permanent) with public staff warnings.
  - SHA-256 media blacklisting to automatically block known prohibited uploads.
  - Comprehensive, tamper-evident audit logs.

---

## Prerequisites

- **Operating System:** Linux (Debian 12+ or Ubuntu 22.04+ recommended)
- **Python:** 3.10 to 3.13
- **Media Utilities:** `ffmpeg` and `ffprobe`
- **Database:** PostgreSQL (recommended for high traffic), MariaDB/MySQL, or SQLite (low traffic)
- **Reverse Proxy:** Nginx

---

## Local Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Ma-ryan/pyuchan.git
   cd pyuchan
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Initialize development environment:**
   ```bash
   export DJANGO_SECRET_KEY="local-dev-insecure-key-change-in-production"
   python manage.py migrate
   python manage.py createsuperuser
   ```

5. **Start background task consumer (Terminal 1):**
   ```bash
   python manage.py djangohuey
   ```

6. **Start Django development server (Terminal 2):**
   ```bash
   python manage.py runserver
   ```

Access the board administration panel at `http://127.0.0.1:8000/admin/` to create your initial boards.

---

## Production Deployment Guide (Debian / Ubuntu)

### 1. System Packages & User Setup

Install system dependencies and create an unprivileged system user:

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev ffmpeg libmagic1 nginx git curl openssl

# Create dedicated service user
sudo useradd -r -s /bin/false -d /opt/pyuchan pyuchan
sudo mkdir -p /opt/pyuchan
sudo chown pyuchan:pyuchan /opt/pyuchan
```

### 2. Cloning & Virtual Environment

Clone the repository into `/opt/pyuchan` and install packages:

```bash
sudo -u pyuchan git clone https://github.com/Ma-ryan/pyuchan.git /opt/pyuchan
cd /opt/pyuchan

sudo -u pyuchan python3 -m venv .venv
sudo -u pyuchan /opt/pyuchan/.venv/bin/pip install --upgrade pip
sudo -u pyuchan /opt/pyuchan/.venv/bin/pip install -r requirements.txt gunicorn
```

Create required media and static asset directories:

```bash
sudo -u pyuchan mkdir -p /opt/pyuchan/media/src
sudo -u pyuchan mkdir -p /opt/pyuchan/media/thumb
sudo -u pyuchan mkdir -p /opt/pyuchan/staticfiles
```

### 3. Configuration & Secrets

Create the production configuration override file `/opt/pyuchan/board/config.py`:

```python
# /opt/pyuchan/board/config.py
import os

# Generate a high-entropy key: openssl rand -hex 32
SECRET_KEY = "REPLACE_WITH_HIGH_ENTROPY_PRODUCTION_SECRET_KEY"

# Board Configuration Overrides
DEFAULT_ANONYMOUS_NAME = "Anonymous"
THREADS_PER_PAGE = 15
ENABLE_CAPTCHA = True
CAPTCHA_ON_THREAD_CREATION = True
CAPTCHA_ON_REPLY = True

# Anti-Abuse
ENABLE_TOR_BLOCKING = True
ENABLE_DNSBL = True
THREAD_COOLDOWN_SECONDS = 30
REPLY_COOLDOWN_SECONDS = 7

# Thread Archival
ENABLE_ARCHIVE = True
ARCHIVE_RETENTION_DAYS = 7
```

Lock down file permissions so only the `pyuchan` user can read secrets:

```bash
sudo chown pyuchan:pyuchan /opt/pyuchan/board/config.py
sudo chmod 600 /opt/pyuchan/board/config.py
```

### 4. Database Migration & Static Files

Generate an encryption key, run database migrations, and collect static files:

```bash
sudo -u pyuchan /opt/pyuchan/.venv/bin/python manage.py migrate
sudo -u pyuchan /opt/pyuchan/.venv/bin/python manage.py collectstatic --noinput
sudo -u pyuchan /opt/pyuchan/.venv/bin/python manage.py createsuperuser
```

Verify that system checks pass cleanly with no configuration warnings:

```bash
sudo -u pyuchan /opt/pyuchan/.venv/bin/python manage.py check --deploy
```

### 5. Systemd Service Units

Production relies on two systemd services:
1. `pyuchan-web.service` runs Gunicorn to handle HTTP requests.
2. `pyuchan-huey.service` runs the worker daemon for asynchronous video transcoding and thumbnail generation.

#### Web Service (`/etc/systemd/system/pyuchan-web.service`)

```ini
[Unit]
Description=pyuchan Gunicorn Web Application
After=network.target

[Service]
Type=notify
User=pyuchan
Group=www-data
WorkingDirectory=/opt/pyuchan
Environment="DJANGO_DEBUG=False"
Environment="DJANGO_ALLOWED_HOSTS=example.com,www.example.com"
ExecStart=/opt/pyuchan/.venv/bin/gunicorn config.wsgi:application \
    --workers 4 \
    --bind unix:/run/pyuchan.sock \
    --access-logfile /var/log/pyuchan-access.log \
    --error-logfile /var/log/pyuchan-error.log \
    --timeout 30
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

#### Huey Task Worker Service (`/etc/systemd/system/pyuchan-huey.service`)

```ini
[Unit]
Description=pyuchan Huey Media Worker
After=network.target

[Service]
User=pyuchan
Group=www-data
WorkingDirectory=/opt/pyuchan
Environment="DJANGO_DEBUG=False"
ExecStart=/opt/pyuchan/.venv/bin/python manage.py djangohuey
KillMode=mixed
TimeoutStopSec=15
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start both services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pyuchan-web pyuchan-huey
```

### 6. Nginx Web Server Configuration

Create the Nginx server block at `/etc/nginx/sites-available/pyuchan`:

```nginx
server {
    listen 80;
    server_name example.com www.example.com;
    client_max_body_size 25M;

    # Static assets directly served by Nginx
    location /static/ {
        alias /opt/pyuchan/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
    }

    # Uploaded files directly served by Nginx
    location /media/ {
        alias /opt/pyuchan/media/;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }

    # Proxy all dynamic requests to Gunicorn socket
    location / {
        include proxy_params;
        proxy_pass http://unix:/run/pyuchan.sock;
        proxy_redirect off;
    }
}
```

Enable the configuration and reload Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/pyuchan /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

*(Optional: Use Certbot to provision free SSL certificates via `sudo certbot --nginx -d example.com`).*

---

### 7. Periodic Maintenance & Cron Jobs

Imageboard maintenance requires periodically purging expired archived threads and updating the Tor exit node cache.

Edit crontab for the `pyuchan` service user:

```bash
sudo crontab -u pyuchan -e
```

Add the following maintenance entries:

```cron
# Daily purge of archived threads exceeding ARCHIVE_RETENTION_DAYS (runs at 04:00 UTC)
0 4 * * * /opt/pyuchan/.venv/bin/python /opt/pyuchan/manage.py purge_archives >> /opt/pyuchan/purge.log 2>&1

# Refresh Tor bulk exit node IP cache once every 24 hours (runs at 03:00 UTC)
0 3 * * * /opt/pyuchan/.venv/bin/python /opt/pyuchan/manage.py sync_tor_exits >> /opt/pyuchan/tor_sync.log 2>&1
```

---

## Wakarimasen Migration

To migrate existing boards from a legacy Wakarimasen (MariaDB/MySQL) installation:

1. Ensure the `pymysql` driver is installed inside the virtual environment:
   ```bash
   pip install PyMySQL
   ```

2. Run the migration command:
   ```bash
   python manage.py migrate_from_wakarimasen \
       --board-uri="b" \
       --board-title="Random" \
       --mysql-db="waka_database" \
       --mysql-user="root" \
       --mysql-pass="secretpassword" \
       --legacy-board-dir="/var/www/wakarimasen/b"
   ```

The script imports threads, posts, timestamps, MD5 hashes, tripcodes, and copies legacy source media and thumbnails into pyuchan while reconstructing reverse backlinks.

---

## License

This software is released under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for complete details.