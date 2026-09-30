# VPS runtime

Keep `/opt/anya/data` on persistent storage: it contains SQLite and downloaded media cache.
Install the project and dependencies as the `anya` user, copy `.env.example` to `.env`, then install the unit:

```bash
sudo cp deploy/anya.service /etc/systemd/system/anya.service
sudo systemctl daemon-reload
sudo systemctl enable --now anya
sudo journalctl -u anya -f
```

Run exactly one service instance against a SQLite database. Back up `data/companion.db` using SQLite's online backup mechanism (or while the service is stopped); do not copy a live WAL database blindly. The service restores `processing` delayed-response jobs on start. Telegram polling remains single-instance by design.
