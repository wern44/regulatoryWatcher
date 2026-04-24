# Deploying RegWatch with Docker

## Prerequisites

- Linux server with Docker and Docker Compose installed
- Network access to your LM Studio server (e.g. `192.168.32.231:1234`)
- Git (to clone the repo)

## 1. Clone the repository

```bash
git clone <your-repo-url> regwatch
cd regwatch
```

## 2. Create your config.yaml

Copy the example and edit it:

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

**Required changes:**

```yaml
llm:
  # Point to your LM Studio server — use the IP reachable from the Docker container
  base_url: "http://192.168.32.231:1234"
  embedding_dim: 768

paths:
  # These paths are INSIDE the container — don't change them
  db_file: "./data/app.db"
  pdf_archive: "./data/pdfs"
  uploads_dir: "./data/uploads"

ui:
  language: en
  timezone: "Europe/Luxembourg"
  # IMPORTANT: must be 0.0.0.0 for Docker, not 127.0.0.1
  host: "0.0.0.0"
  port: 8001
```

Make sure `ui.host` is set to `"0.0.0.0"` — otherwise the container won't accept connections from outside.

## 3. Build and start

```bash
docker compose up -d --build
```

This will:
1. Build the Docker image (~2-3 minutes on first run)
2. Start the container in the background
3. On first start, automatically initialise the database and load the seed catalog
4. Start the web UI on port 8001

## 4. Verify it's running

```bash
# Check container status
docker compose ps

# View logs
docker compose logs -f regwatch

# Should show "Starting RegWatch on port 8001..."
```

Open your browser and go to `http://<your-server-ip>:8001`.

On first visit, you'll be redirected to the setup page to select your LLM models.

## 5. Automatic update checks

The built-in scheduler runs inside the web process and checks all enabled regulatory sources on a configurable frequency. **No external cron or systemd timer is needed.**

By default, it runs **every 2 days at 06:00**. To change this:

1. Open `http://<your-server-ip>:8001/settings`
2. Find the **Scheduled Updates** section
3. Adjust the frequency (Every 4 hours / Daily / Every 2 days / Weekly / Monthly), preferred time, and enable/disable toggle
4. Click **Save schedule**

Settings are stored in the database and survive container restarts. The scheduler starts automatically with the web UI — as long as the container is running, updates will be fetched on schedule.

If you prefer to trigger pipeline runs externally (e.g. via cron while the container is stopped), you can pause the built-in scheduler in Settings and use:

```bash
docker compose exec regwatch regwatch run-pipeline
```

## Common operations

### View logs

```bash
docker compose logs -f regwatch
```

### Restart

```bash
docker compose restart
```

### Stop

```bash
docker compose down
```

### Update to latest code

```bash
git pull
docker compose up -d --build
```

Your data is stored in a Docker volume (`regwatch-data`) and survives rebuilds.

### Run CLI commands inside the container

```bash
docker compose exec regwatch regwatch run-pipeline
docker compose exec regwatch regwatch chat "What is DORA?"
docker compose exec regwatch regwatch dump-pipeline-runs --tail 10
```

### Backup the database

```bash
# Copy the database file out of the container
docker compose exec regwatch regwatch db-export --output /app/data/backup.db
docker compose cp regwatch:/app/data/backup.db ./backup.db
```

### Restore a database backup

```bash
docker compose cp ./backup.db regwatch:/app/data/backup.db
docker compose exec regwatch regwatch db-import /app/data/backup.db --yes
docker compose restart
```

### Reset the database

```bash
docker compose exec regwatch regwatch db-reset --yes
docker compose restart
```

## Data persistence

All application data is stored in the `regwatch-data` Docker volume:

| Path (in container) | Content |
|---------------------|---------|
| `/app/data/app.db` | SQLite database (regulations, events, settings, chat) |
| `/app/data/pdfs/` | Archived PDF documents |
| `/app/data/uploads/` | Manually uploaded PDFs |

To fully reset (delete all data):

```bash
docker compose down -v   # -v removes volumes
docker compose up -d --build
```

## Networking

The container needs to reach your LM Studio server. If LM Studio runs on a different machine on the same LAN, use its LAN IP in `config.yaml` (not `localhost`).

If your Docker host has a firewall, ensure port 8001 is open:

```bash
sudo ufw allow 8001/tcp
```

## Troubleshooting

**"Connection refused" when opening the UI:**
- Check `ui.host` is `"0.0.0.0"` in config.yaml (not `127.0.0.1`)
- Check the container is running: `docker compose ps`
- Check logs: `docker compose logs regwatch`

**"LLM server unreachable" in Settings:**
- Verify LM Studio is running and accessible from the Docker host: `curl http://192.168.32.231:1234/v1/models`
- If using Docker Desktop on Mac/Windows, use `host.docker.internal` instead of a LAN IP

**Database locked errors:**
- Only one container should run at a time (SQLite is single-writer)
- Don't run CLI commands while the container is starting up
