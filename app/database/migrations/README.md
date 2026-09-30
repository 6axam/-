# Migrations

Версия MVP создаёт начальную SQLite-схему идемпотентно при старте в `app/database/db.py`.
При переходе на production перенесите SQL в версионируемые Alembic-миграции.
