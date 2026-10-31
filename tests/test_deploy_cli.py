import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.deploy import cli
from app.deploy.cli import (
    _compose, _database_path, _ensure_clean, _resolve_commit, check_migration_is_additive,
    sqlite_backup, _values,
)


def _database(path, *, extra_column=False, changed_value=None):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    db.execute("INSERT INTO contacts(id,name) VALUES(1,?)", (changed_value or "Maks",))
    if extra_column:
        db.execute("ALTER TABLE contacts ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    db.commit()
    db.close()


def test_sqlite_backup_uses_consistent_copy(tmp_path):
    source, backup = tmp_path / "source.db", tmp_path / "backup.db"
    _database(source)
    sqlite_backup(source, backup)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert db.execute("SELECT name FROM contacts").fetchone() == ("Maks",)


def test_migration_accepts_additive_column_without_rewriting_old_data(tmp_path):
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    _database(before)
    _database(after, extra_column=True)
    check_migration_is_additive(before, after)


def test_migration_rejects_rewriting_existing_data(tmp_path):
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    _database(before)
    _database(after, extra_column=True, changed_value="Changed")
    with pytest.raises(RuntimeError, match="changed_existing_rows"):
        check_migration_is_additive(before, after)


def test_migration_rejects_dropped_columns(tmp_path):
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    _database(before)
    with sqlite3.connect(after) as db:
        db.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO contacts(id) VALUES(1)")
    with pytest.raises(RuntimeError, match="removed_columns"):
        check_migration_is_additive(before, after)


def test_migration_rejects_changed_column_constraints(tmp_path):
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    _database(before)
    db = sqlite3.connect(after)
    db.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO contacts(id,name) VALUES(1,'Maks')")
    db.commit(); db.close()
    with pytest.raises(RuntimeError, match="changed_table_definitions"):
        check_migration_is_additive(before, after)


def test_perf_percentile_uses_nearest_rank():
    assert _values([1, 2, 3, 4, 100]) == (22.0, 100)
    assert _values([]) is None


def test_database_path_is_kept_inside_persistent_directory(tmp_path):
    env = tmp_path / "runtime.env"
    env.write_text("DATABASE_URL=sqlite:///data/companion.db\n")
    assert _database_path({"env": env, "data": tmp_path / "data"}) == tmp_path / "data" / "companion.db"
    env.write_text("DATABASE_URL=sqlite:////etc/passwd\n")
    with pytest.raises(RuntimeError, match="inside /app/data"):
        _database_path({"env": env, "data": tmp_path / "data"})


def test_deploy_resolves_only_available_commit_shas(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "file.txt").write_text("commit\n")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "test"], check=True)
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert _resolve_commit(repo, sha[:8]) == sha
    with pytest.raises(RuntimeError, match="hexadecimal SHA"):
        _resolve_commit(repo, "--help")
    with pytest.raises(RuntimeError, match="not available"):
        _resolve_commit(repo, "f" * 40)


def test_deploy_refuses_dirty_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "untracked.txt").write_text("preserve me\n")
    with pytest.raises(RuntimeError, match="dirty"):
        _ensure_clean(repo)


def test_compose_keeps_build_paths_relative_to_compose_file(tmp_path, monkeypatch):
    root, repo = tmp_path / "instance", tmp_path / "repo"
    root.mkdir(); repo.mkdir()
    compose = repo / "deploy" / "compose.yaml"
    compose.parent.mkdir(); compose.write_text("services: {}\n")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs["cwd"]
        env_index = argv.index("--env-file") + 1
        captured["env_file_exists"] = Path(argv[env_index]).is_file()
        return ""

    monkeypatch.setattr("app.deploy.cli._run", fake_run)
    _compose({"root": root, "repo": repo, "data": root / "data", "env": root / "runtime.env",
              "state": root / "state.json", "compose": compose}, "build", "anya",
             image_tag="a" * 40)
    assert "--project-directory" not in captured["argv"]
    assert captured["cwd"] == repo
    assert captured["env_file_exists"]
    assert not list(root.glob(".anya-compose-*.env"))


def test_runtime_image_keeps_private_runtime_files_out_of_build_context():
    root = Path(cli.__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    ignore = (root / ".dockerignore").read_text().splitlines()
    assert "COPY --chown=1000:1000 app ./app" in dockerfile
    assert "COPY --chown=1000:1000 prompts ./prompts" in dockerfile
    assert {".env", "data", "assets"} <= set(ignore)


def test_perf_separates_llm_and_scheduled_waits_from_full_reply(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data" / "companion.db"
    db_path.parent.mkdir()
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE conversation_turns(id INTEGER PRIMARY KEY,chat_id INTEGER,created_at TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id INTEGER,conversation_turn_id INTEGER,
                              sender TEXT,timestamp TEXT,telegram_message_id INTEGER);
        CREATE TABLE llm_generations(id INTEGER PRIMARY KEY,turn_id INTEGER,generation_id TEXT,
                                     kind TEXT,latency_ms INTEGER);
        CREATE TABLE scheduled_responses(id INTEGER PRIMARY KEY,generation_id TEXT,respond_after TEXT,
                                         created_at TEXT,status TEXT);
        CREATE TABLE scheduled_reads(id INTEGER PRIMARY KEY,chat_id INTEGER,boundary_message_id INTEGER,
                                     read_after TEXT,created_at TEXT,status TEXT);
        INSERT INTO conversation_turns VALUES(1,10,'2026-10-31 12:00:00');
        INSERT INTO messages VALUES(1,10,1,'user','2026-10-31 12:00:00',101);
        INSERT INTO messages VALUES(2,10,1,'assistant','2026-10-31 12:00:10',102);
        INSERT INTO llm_generations VALUES(1,1,'generation-1','conversation',3000);
        INSERT INTO scheduled_responses VALUES(1,'generation-1','2026-10-31 12:00:04','2026-10-31 12:00:01','completed');
        INSERT INTO scheduled_reads VALUES(1,10,101,'2026-10-31 12:00:01','2026-10-31 12:00:00','completed');
    """)
    db.commit(); db.close()
    paths = {"data": db_path.parent, "env": tmp_path / "runtime.env"}
    monkeypatch.setattr(cli, "_run", lambda *args, **kwargs: "anya|5.0%|120MiB / 4GiB\n")
    cli._perf(paths)
    output = capsys.readouterr().out
    assert "OpenRouter request: avg=3000.0 p95=3000.0 n=1" in output
    assert "full user-message to reply: avg=10000.0 p95=10000.0 n=1" in output
    assert "scheduled reply delay: avg=3000.0 p95=3000.0 n=1" in output
    assert "scheduled read delay: avg=1000.0 p95=1000.0 n=1" in output
    assert "local+Telegram delivery residual (estimate): avg=3000.0 p95=3000.0 n=1" in output
