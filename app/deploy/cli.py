"""Safe, manual Docker Compose operations for the Mira Anya deployment."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
HEALTH_TIMEOUT_SECONDS = 150


def _paths() -> dict[str, Path]:
    repo = Path(__file__).resolve().parents[2]
    root = Path(os.environ.get("ANYA_HOME", repo.parent)).resolve()
    return {
        "repo": repo,
        "root": root,
        "data": Path(os.environ.get("ANYA_DATA_DIR", root / "data")).resolve(),
        "env": Path(os.environ.get("ANYA_RUNTIME_ENV_FILE", root / "runtime.env")).resolve(),
        "backups": root / "backups",
        "state": root / "deploy-state.json",
        "lock": root / ".deploy.lock",
        "compose": repo / "deploy" / "compose.yaml",
    }


def _run(argv: list[str], *, cwd: Path | None = None, capture: bool = False, check: bool = True) -> str:
    result = subprocess.run(
        argv, cwd=cwd, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode:
        details = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {argv[0]}" + (f"\n{details}" if details else ""))
    return result.stdout or ""


def _git(repo: Path, *args: str, capture: bool = True, check: bool = True) -> str:
    return _run(["git", "-C", str(repo), *args], capture=capture, check=check)


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Deployment state is unreadable: {path}") from exc


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _runtime_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key:
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


def _database_path(paths: dict[str, Path]) -> Path:
    value = _runtime_value(paths["env"], "DATABASE_URL") or "sqlite:///data/companion.db"
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if value.startswith(prefix):
            raw = value[len(prefix):]
            break
    else:
        raise RuntimeError("Docker deployment requires DATABASE_URL=sqlite:///...")
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to("/app/data")
        except ValueError:
            if candidate.parts[:2] == ("/", "data"):
                relative = Path(*candidate.parts[2:])
            else:
                raise RuntimeError("DATABASE_URL must point inside /app/data in the container")
    else:
        relative = candidate
        if relative.parts and relative.parts[0] == "data":
            relative = Path(*relative.parts[1:])
    if not relative.parts or ".." in relative.parts:
        raise RuntimeError("DATABASE_URL resolves outside the persistent data directory")
    return paths["data"] / relative


def sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    if not source.is_file():
        raise RuntimeError(f"SQLite database not found: {source}")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(destination, timeout=30)
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"SQLite backup failed quick_check: {check}")
    finally:
        dst.close()
        src.close()
    os.chmod(destination, 0o600)


def _table_clauses(sql: str) -> set[str]:
    start = sql.find("(")
    if start < 0:
        return {" ".join(sql.split()).casefold()}
    clauses: list[str] = []
    depth = 0
    quote = None
    begin = start + 1
    i = begin
    while i < len(sql):
        char = sql[i]
        if quote:
            if char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif char in "\"'`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                clause = " ".join(sql[begin:i].strip().split()).casefold()
                if clause:
                    clauses.append(clause)
                break
            depth -= 1
        elif char == "," and depth == 0:
            clause = " ".join(sql[begin:i].strip().split()).casefold()
            if clause:
                clauses.append(clause)
            begin = i + 1
        i += 1
    return set(clauses)


def _schema(path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[tuple[str, str], str]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        columns: dict[str, set[str]] = {}
        definitions: dict[str, set[str]] = {}
        for table in tables:
            info = list(con.execute(f'PRAGMA table_info("{table}")'))
            names = [r[1] for r in info]
            columns[table] = set(names)
            sql_row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            definitions[table] = _table_clauses(sql_row[0] or "") if sql_row else set()
        objects = {
            (r[0], r[1]): (r[2] or "") for r in con.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','trigger','view') AND name NOT LIKE 'sqlite_%'"
            )
        }
        return columns, definitions, objects
    finally:
        con.close()


def _table_digest(path: Path, table: str, columns: set[str]) -> str:
    if not columns:
        return hashlib.sha256(b"").hexdigest()
    selected = sorted(columns)
    quoted = ",".join(f'"{column}"' for column in selected)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {quoted}')
        digest = hashlib.sha256()
        for row in rows:
            encoded = json.dumps(tuple(row), ensure_ascii=False, sort_keys=True, default=str)
            digest.update(encoded.encode("utf-8")); digest.update(b"\n")
        return digest.hexdigest()
    finally:
        con.close()


def check_migration_is_additive(before: Path, after: Path) -> None:
    old_columns, old_definitions, old_objects = _schema(before)
    new_columns, new_definitions, new_objects = _schema(after)
    missing_tables = set(old_columns) - set(new_columns)
    missing_columns = {
        table: sorted(cols - new_columns.get(table, set()))
        for table, cols in old_columns.items()
        if cols - new_columns.get(table, set())
    }
    removed_objects = set(old_objects) - set(new_objects)
    changed_objects = {
        item for item, definition in old_objects.items()
        if item in new_objects and " ".join(definition.split()).casefold() != " ".join(new_objects[item].split()).casefold()
    }
    changed_table_definitions = {
        table: sorted(clauses - new_definitions.get(table, set()))
        for table, clauses in old_definitions.items()
        if clauses - new_definitions.get(table, set())
    }
    changed_data = [
        table for table, columns in old_columns.items()
        if table != "schema_migrations" and table in new_columns and columns <= new_columns[table]
        and _table_digest(before, table, columns) != _table_digest(after, table, columns)
    ]
    if missing_tables or missing_columns or removed_objects or changed_objects or changed_table_definitions or changed_data:
        raise RuntimeError(
            "Database migration is not rollback-safe: "
            f"removed_tables={sorted(missing_tables)}, removed_columns={missing_columns}, "
            f"removed_objects={sorted(removed_objects)}, changed_objects={sorted(changed_objects)}, "
            f"changed_table_definitions={changed_table_definitions}, changed_existing_rows={changed_data}"
        )


def _compose(paths: dict[str, Path], *args: str, image_tag: str | None = None, check: bool = True, capture: bool = False) -> str:
    tag = image_tag or _load_state(paths["state"]).get("current_commit") or "local"
    values = {
        "ANYA_IMAGE_TAG": tag,
        "ANYA_RUNTIME_ENV_FILE": str(paths["env"]),
        "ANYA_DATA_DIR": str(paths["data"]),
    }
    env = "".join(f"{key}={value}\n" for key, value in values.items())
    fd, name = tempfile.mkstemp(prefix=".anya-compose-", suffix=".env", dir=paths["root"])
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(env)
        os.chmod(name, 0o600)
        return _run(
            ["docker", "compose", "--project-name", "anya", "--env-file", name,
             "-f", str(paths["compose"]), *args],
            cwd=paths["repo"], capture=capture, check=check,
        )
    finally:
        Path(name).unlink(missing_ok=True)


def _ensure_clean(repo: Path) -> None:
    dirty = _git(repo, "status", "--porcelain", capture=True).strip()
    if dirty:
        raise RuntimeError("Deployment checkout is dirty; refusing to overwrite local changes")


def _resolve_commit(repo: Path, value: str) -> str:
    if not COMMIT_RE.fullmatch(value):
        raise RuntimeError("Commit must be a 7–40 character hexadecimal SHA")
    try:
        resolved = _git(repo, "rev-parse", "--verify", f"{value.lower()}^{{commit}}").strip()
    except RuntimeError as exc:
        raise RuntimeError("Requested commit is not available in the repository") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise RuntimeError("Requested commit is not available in the repository")
    return resolved


def _run_staged_migrations(paths: dict[str, Path], target: str, current: str | None) -> None:
    stage = paths["data"] / f".anya-migration-stage-{os.getpid()}.sqlite"
    live = _database_path(paths)
    try:
        sqlite_backup(live, stage)
        before = paths["data"] / f".anya-migration-before-{os.getpid()}.sqlite"
        shutil.copy2(stage, before)
        migration_check = _database_probe(stage.name)
        _compose(paths, "run", "--rm", "--no-deps", "-e", "DATABASE_URL=sqlite:///data/" + stage.name,
                 "anya", "python", "-c", migration_check, image_tag=target)
        check_migration_is_additive(before, stage)
        if current and current != target:
            _compose(paths, "run", "--rm", "--no-deps", "-e", "DATABASE_URL=sqlite:///data/" + stage.name,
                     "anya", "python", "-c", migration_check,
                     image_tag=current)
    finally:
        stage.unlink(missing_ok=True)
        (paths["data"] / f".anya-migration-before-{os.getpid()}.sqlite").unlink(missing_ok=True)


def _database_probe(filename: str) -> str:
    return (
        "import asyncio\nfrom app.database.db import Database\n"
        "async def main():\n"
        f"    db=Database('sqlite:///data/{filename}')\n"
        "    await db.connect()\n"
        "    await db.close()\n"
        "asyncio.run(main())"
    )


def _wait_healthy() -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _run(["docker", "inspect", "--format", "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}", "anya"], capture=True, check=False).strip()
        if state.endswith("|healthy"):
            return True
        if state.startswith("exited|") or state.startswith("dead|"):
            return False
        time.sleep(3)
    return False


def _image_id(tag: str | None) -> str | None:
    if not tag:
        return None
    value = _run(["docker", "image", "inspect", "--format", "{{.Id}}", f"anya:{tag}"], capture=True, check=False).strip()
    return value or None


def _backup_database(paths: dict[str, Path], target: str) -> Path:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = paths["backups"] / f"companion-{now}-{target[:12]}.sqlite"
    sqlite_backup(_database_path(paths), backup)
    return backup


def _deploy(paths: dict[str, Path], target: str) -> None:
    repo = paths["repo"]
    _ensure_clean(repo)
    target = _resolve_commit(repo, target)
    state = _load_state(paths["state"])
    current = state.get("current_commit")
    previous_image_id = _image_id(current) or state.get("current_image_id")
    if current == target and _wait_healthy():
        print(f"Already deployed and healthy: {target}")
        return

    old_head = _git(repo, "rev-parse", "HEAD").strip()
    backup = None
    if _database_path(paths).exists():
        backup = str(_backup_database(paths, target))
    try:
        _git(repo, "checkout", "--detach", target, capture=False)
        _compose(paths, "build", "--pull", "anya", image_tag=target)
        target_image_id = _image_id(target)
        _run_staged_migrations(paths, target, current)
        before_state = {
            **state,
            "previous_commit": current,
            "previous_image": f"anya:{current}" if current else None,
            "previous_image_id": previous_image_id,
            "current_commit": target,
            "current_image_id": target_image_id,
            "database_backup": backup,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(paths["state"], before_state)
        _compose(paths, "up", "--detach", "--no-deps", "--force-recreate", "anya", image_tag=target)
        if not _wait_healthy():
            raise RuntimeError("New container failed its healthcheck")
        print(f"Deployed and healthy: {target}")
    except Exception as exc:
        if current:
            try:
                _compose(paths, "up", "--detach", "--no-deps", "--force-recreate", "anya", image_tag=current)
                if not _wait_healthy():
                    raise RuntimeError("previous container did not become healthy")
                _write_json_atomic(paths["state"], {
                    **state,
                    "current_commit": current,
                    "current_image_id": previous_image_id,
                    "failed_commit": target,
                    "database_backup": backup,
                    "rollback_at": datetime.now(timezone.utc).isoformat(),
                })
                print(f"Deployment failed; previous version restored: {current}", file=sys.stderr)
            except Exception as rollback_exc:
                _write_json_atomic(paths["state"], {
                    **state,
                    "current_commit": current,
                    "current_image_id": previous_image_id,
                    "failed_commit": target,
                    "rollback_error": str(rollback_exc),
                    "failure_at": datetime.now(timezone.utc).isoformat(),
                })
                print(f"AUTOMATIC ROLLBACK FAILED: {rollback_exc}", file=sys.stderr)
        else:
            _compose(paths, "stop", "anya", image_tag=target, check=False)
            _write_json_atomic(paths["state"], {
                "current_commit": None,
                "failed_commit": target,
                "database_backup": backup,
                "failure_at": datetime.now(timezone.utc).isoformat(),
            })
        raise exc
    finally:
        # Keep the checkout at the deployed revision after success or restore it
        # to the previous deployed revision after a failed rollout.
        final = _load_state(paths["state"]).get("current_commit", old_head)
        if final and _git(repo, "rev-parse", "HEAD").strip() != final:
            _git(repo, "checkout", "--detach", final, capture=False, check=False)


def _status(paths: dict[str, Path]) -> None:
    state = _load_state(paths["state"])
    commit = state.get("current_commit") or _git(paths["repo"], "rev-parse", "HEAD").strip()
    changelog = paths["repo"] / "CHANGELOG.md"
    version = "unknown"
    if changelog.is_file():
        match = re.search(r"^## (v[^ ]+)", changelog.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            version = match.group(1)
    print(f"version: {version}")
    print(f"commit: {commit}")
    _compose(paths, "ps", image_tag=state.get("current_commit") or "local", check=False)
    inspected = _run(["docker", "inspect", "--format", "{{json .State}}", "anya"], capture=True, check=False).strip()
    if inspected:
        try:
            runtime = json.loads(inspected)
            started = datetime.fromisoformat(runtime["StartedAt"].replace("Z", "+00:00"))
            uptime = str(datetime.now(timezone.utc) - started) if runtime.get("Status") == "running" else "—"
            health = (runtime.get("Health") or {}).get("Status", "n/a")
            print(f"container: {runtime.get('Status')} health={health} uptime={uptime}")
        except (KeyError, ValueError, TypeError):
            print(f"container state: {inspected}")
    else:
        print("container: not found")


def _values(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    p95 = ordered[max(0, int(0.95 * len(ordered) + 0.999999) - 1)]
    return statistics.fmean(ordered), p95


def _perf(paths: dict[str, Path]) -> None:
    db_path = _database_path(paths)
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        llm = [float(r[0]) for r in db.execute(
            "SELECT latency_ms FROM llm_generations WHERE kind='conversation' ORDER BY id DESC LIMIT 500"
        )]
        turn_rows = db.execute(
            "SELECT t.id,t.chat_id,MIN(CASE WHEN m.sender='user' THEN m.timestamp END) AS first_user,"
            "MAX(CASE WHEN m.sender='assistant' THEN m.timestamp END) AS last_assistant "
            "FROM conversation_turns t JOIN messages m ON m.conversation_turn_id=t.id "
            "GROUP BY t.id HAVING first_user IS NOT NULL AND last_assistant IS NOT NULL "
            "ORDER BY t.id DESC LIMIT 500"
        ).fetchall()
        turn_ids = [int(row["id"]) for row in turn_rows]
        if turn_ids:
            marks = ",".join("?" for _ in turn_ids)
            llm_by_turn = {r[0]: float(r[1]) for r in db.execute(
                f"SELECT turn_id,SUM(latency_ms) FROM llm_generations WHERE kind='conversation' "
                f"AND turn_id IN ({marks}) GROUP BY turn_id", turn_ids,
            )}
            generation_rows = db.execute(
                f"SELECT generation_id,turn_id FROM llm_generations WHERE generation_id IS NOT NULL AND turn_id IN ({marks})",
                turn_ids,
            ).fetchall()
            message_rows = db.execute(
                f"SELECT conversation_turn_id,sender,timestamp FROM messages WHERE conversation_turn_id IN ({marks}) "
                "ORDER BY conversation_turn_id,timestamp,id", turn_ids,
            ).fetchall()
        else:
            llm_by_turn, generation_rows, message_rows = {}, [], []
        reply_delay_by_generation = {r[0]: max(0.0, (datetime.fromisoformat(r[1]) - datetime.fromisoformat(r[2])).total_seconds() * 1000)
                                     for r in db.execute("SELECT generation_id,respond_after,created_at FROM scheduled_responses WHERE status='completed' ORDER BY id DESC LIMIT 1000")}
        generation_turn = {r[0]: r[1] for r in generation_rows}
        reply_delay_by_turn: dict[int, float] = {}
        for generation, delay in reply_delay_by_generation.items():
            turn = generation_turn.get(generation)
            if turn is not None:
                reply_delay_by_turn[turn] = reply_delay_by_turn.get(turn, 0.0) + delay
        read_delay_by_turn: dict[int, float] = {}
        if turn_ids:
            for row in db.execute(
                f"SELECT m.conversation_turn_id,s.read_after,s.created_at FROM scheduled_reads s "
                f"JOIN messages m ON m.chat_id=s.chat_id AND m.telegram_message_id=s.boundary_message_id "
                f"WHERE s.status='completed' AND m.conversation_turn_id IN ({marks}) ORDER BY s.id DESC LIMIT 1000",
                turn_ids,
            ):
                read_delay_by_turn[row[0]] = max(0.0, (datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[2])).total_seconds() * 1000)
        first_user: dict[int, str] = {}
        last_assistant: dict[int, str] = {}
        for event in message_rows:
            turn = int(event[0])
            if event[1] == "user":
                first_user.setdefault(turn, event[2])
            elif event[1] == "assistant":
                last_assistant[turn] = event[2]
        full_by_turn = {
            turn: max(0.0, (datetime.fromisoformat(last_assistant[turn]) - datetime.fromisoformat(start)).total_seconds() * 1000)
            for turn, start in first_user.items() if turn in last_assistant
        }
        full = list(full_by_turn.values())
        response_delays = list(reply_delay_by_turn.values())
        read_delays = list(read_delay_by_turn.values())
        local = [max(0.0, total - llm_by_turn.get(turn, 0.0) - reply_delay_by_turn.get(turn, 0.0) - read_delay_by_turn.get(turn, 0.0))
                 for turn, total in full_by_turn.items()]
    finally:
        db.close()
    print("Performance over up to the last 500 samples (ms; averages and P95):")
    for label, samples in (("OpenRouter request", llm), ("full user-message to reply", full),
                           ("scheduled reply delay", response_delays), ("scheduled read delay", read_delays)):
        result = _values(samples)
        print(f"{label}: avg={result[0]:.1f} p95={result[1]:.1f} n={len(samples)}" if result else f"{label}: no samples")
    result = _values(local)
    print(f"local+Telegram delivery residual (estimate): avg={result[0]:.1f} p95={result[1]:.1f} n={len(local)}" if result else "local+Telegram delivery residual (estimate): no matched samples")
    raw = _run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"], capture=True, check=False)
    line = next((line for line in raw.splitlines() if line.startswith("anya|")), None)
    print(f"container: {line.replace('|', ' ')}" if line else "container: unavailable")


def _run_locked(command, paths: dict[str, Path]) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock"].open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another anya deployment command is running") from exc
        command(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anya", description="Manual Anya Docker deployment control")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("update", help="fetch origin/master and deploy the latest commit")
    commands.add_parser("rollback", help="restore the previous healthy deployment")
    deploy_parser = commands.add_parser("deploy", help="deploy an available commit SHA")
    deploy_parser.add_argument("commit")
    commands.add_parser("status", help="show the deployed revision and container health")
    commands.add_parser("logs", help="tail Anya container logs")
    commands.add_parser("perf", help="show response and container performance")
    args = parser.parse_args(argv)
    paths = _paths()
    try:
        if not paths["repo"].is_dir() or not paths["compose"].is_file():
            raise RuntimeError("Anya repository or Docker Compose file is missing")
        if args.command == "status":
            _status(paths)
        elif args.command == "logs":
            _compose(paths, "logs", "--tail=200", "--follow", "anya", check=False)
        elif args.command == "perf":
            _perf(paths)
        else:
            def deploy_command(locked_paths: dict[str, Path]) -> None:
                if args.command in {"update", "deploy"}:
                    _ensure_clean(locked_paths["repo"])
                    _git(locked_paths["repo"], "fetch", "--prune", "origin", "master", capture=False)
                    target = (_git(locked_paths["repo"], "rev-parse", "refs/remotes/origin/master").strip()
                              if args.command == "update" else _resolve_commit(locked_paths["repo"], args.commit))
                else:
                    state = _load_state(locked_paths["state"])
                    target = state.get("previous_commit")
                    if not target:
                        raise RuntimeError("No previous successful deployment is recorded")
                    _resolve_commit(locked_paths["repo"], target)
                _deploy(locked_paths, target)
            _run_locked(deploy_command, paths)
    except (OSError, RuntimeError, subprocess.SubprocessError, sqlite3.Error) as exc:
        print(f"anya: {exc}", file=sys.stderr)
        return 1
    return 0
