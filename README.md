# ACNH Tracker

An independent, English-language Animal Crossing: New Horizons collection tracker designed for a Raspberry Pi 4 (8 GB) or another Docker host.

- **Runtime:** Python 3.12, Flask, Gunicorn, SQLite, HTML/CSS and JavaScript
- **One Docker container:** no Node.js runtime, external database, or Home Assistant required
- **Data:** six collection categories (939 bundled collectibles) and bundled artwork (923 images / 887 linked items)
- **Per-user:** accounts, checkmarks, hemisphere, timezone, checklist preferences, and theme
- **Tracker:** seasonal/current-time availability, live sidebar filters, typo-tolerant search, compact cards, larger Art cards, DIY progressive browsing and event/checklist pages
- **Recovery:** personal progress export/import, timestamped complete backup, database backup, admin-only verified restore, and persistent `/data` storage

## Deploy to a Raspberry Pi with Portainer

Source: `https://github.com/LuxtineSDG470/acnh-tracker`  
Image: `ghcr.io/luxtinesdg470/acnh-tracker:latest`

1. Commit the project to the repository's `main` branch, **including** `.github/workflows/docker-publish.yml`, `bundle/catalog.json` and `bundle/artwork-pack.zip`.
2. Wait for the GitHub Actions build, tests and multi-architecture (ARM64/AMD64) image publication to succeed. Set the GHCR package to public, or configure Portainer with registry credentials.
3. In Portainer, deploy from the repository using `compose.portainer.yaml`. Its external `shared-net` network must already exist; use `compose.yaml` for a host without that network.
4. By default open `http://YOUR-PI-IP:5050` (adjust `ACNH_PORT` to change it). Register the first administrator account and sign in. All built-in collections load automatically.
5. To update, push to GitHub, wait for a successful image build, and **re-pull/redeploy** in Portainer. A container restart by itself does not pull a new image.

The application's data directory is mounted separately as `/opt/acnh-tracker-data:/data`. **Never remove or replace it while updating the code.** The container prepares the bind mount at startup, then runs as UID 10001. A new install does not need manual `chown` on a normal local Docker filesystem.

See [PORTAINER_SETUP.md](PORTAINER_SETUP.md) for setup and network details.

## Developer checks

```bash
python -m unittest discover -s tests -v
python scripts/verify_bundle.py
node --check app/static/app.js
```

The `tests/` folder is **development/CI code**. It is needed in GitHub but is not copied into the deployed Docker image. GitHub Actions runs those tests before publishing. `scripts/verify_bundle.py` checks all packaged artwork paths against catalog items. There is no dependency on the old tracker source or migration modules.

## Data and safety

- Your checkmarks, accounts, backups and locally saved artwork live in the Docker volume, not the GitHub repository.
- Exported full-backup ZIPs and SQLite backups contain private account data and password hashes. Keep them private.
- A full backup restore replaces current user data. Preview/validate it first and test recovery on a separate installation.
- Import/export of current **collection progress** remains supported. The retired previous-application source ZIP importer, standalone artwork uploader, and previous-app SQLite migrator are not part of this release.
- The bundled catalog/artwork are release assets. Only publicly distribute imagery and data for which you have appropriate rights.
- The application is an unofficial fan project and is not affiliated with Nintendo.

## Releases

See [CHANGELOG.md](CHANGELOG.md) for history and [RELEASE_NOTES_v0.1.20.md](RELEASE_NOTES_v0.1.20.md) for the cleanup details. This is a cleanup release, **not a schema change**.
