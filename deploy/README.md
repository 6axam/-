# Mira deployment

Anya is run as one Docker Compose service. The Python container has no Docker
socket and cannot run shell commands. The manual `anya` host CLI is the only
deployment controller; it accepts fixed operations and a hexadecimal commit
SHA. No GitHub webhook or push-triggered deploy is installed.

## Persistent files

Keep the full Git clone at `/home/mira/anya/repo`, the private environment file
at `/home/mira/anya/runtime.env`, and all writable runtime state at
`/home/mira/anya/data`. The data directory contains SQLite, the Telethon
session, emotion history, downloaded media, and local model cache. Backups are
stored separately in `/home/mira/anya/backups`. None of these paths is inside
the image or committed to Git.

Set `mira` up to control Docker (membership in the `docker` group or equivalent
sudo access is required), then clone the complete repository history. Keep the
runtime environment and state out of the clone:

```bash
mkdir -p /home/mira/anya/{data,backups}
chmod 700 /home/mira/anya /home/mira/anya/data /home/mira/anya/backups
git clone https://github.com/6axam/-.git /home/mira/anya/repo
chmod 600 /home/mira/anya/runtime.env
mkdir -p /home/mira/.local/bin
ln -sfn /home/mira/anya/repo/deploy/anya /home/mira/.local/bin/anya
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' /home/mira/.profile || \
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> /home/mira/.profile
```

Open a new login shell (or run `source ~/.profile`) before using `anya`.

Place the existing `.env` contents in `runtime.env` using a secure file copy,
then transfer the existing `data/` contents and private
`assets/anya/reference.jpg` / `assets/anya/voice_reference.wav` into
`/home/mira/anya/data/assets/anya/`. Create the SQLite transfer with Python's
`sqlite3.Connection.backup()` before copying it;
do not copy a live SQLite database/WAL pair as ordinary files. Do not copy
`*.session.lock`. Preserve `data/mtproto/anya.session` as a credential with
mode `0600`. Keep the values for `TELEGRAM_TRANSPORT=mtproto`, the Telegram
allowlist, MTProto credentials, and provider keys unchanged.

After the account is configured to manage Docker and the environment/data are
present, deploy a chosen commit:

```bash
cd /home/mira/anya/repo
anya deploy "$(git rev-parse HEAD)"
```

Compose restarts the container after host reboot and on process failure. Its
healthcheck verifies the app's private database-health endpoint; logs rotate at
10 MB with three files retained. The app container is non-root, read-only
except for `/app/data` and `/tmp`, has a 4 GB memory ceiling and two-CPU limit,
and publishes no ports. Only one container named `anya` can run. Telethon also
locks the persistent session file against a second local client.

## Manual commands

```text
anya update           # fetch origin/master, build and deploy it
anya deploy <commit>  # deploy an available full or abbreviated SHA
anya rollback         # restore the previous healthy deployed commit
anya status           # version, SHA, container health and uptime
anya logs             # follow the latest 200 container log lines
anya perf             # average/P95 timings and current CPU/RAM
```

Before replacing a container, `anya` makes an online SQLite backup and tests
the new image's migrations on a copy of the database. It rejects removed
tables/columns/indexes and changes to existing rows, then verifies that the
previous image can still open the migrated copy. It builds before stopping the
current service. A failed healthcheck automatically starts the previous image
again. Backups are retained under `/home/mira/anya/backups`; the rollback
therefore never restores an older database over newer conversation data.

Use `anya status` and `anya logs` before retrying a failed deployment. A failed
first installation has no previous Docker image to restore; the copied source
database and its pre-deploy backup remain in place for recovery.
