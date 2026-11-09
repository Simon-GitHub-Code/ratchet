# How production runs

- One VM. nginx in front, proxying to `gunicorn -w 4 pinboard.app:application`: four worker processes, and nginx spreads requests across them with no stickiness.
- Deploys happen several times a day. Each deploy restarts the gunicorn workers.
- The database is one SQLite file at `/var/lib/pinboard/pinboard.db`, opened in WAL mode. Peak load is about 20 requests a second, roughly one write in ten.
- Backups copy the database file nightly.
