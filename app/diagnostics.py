"""Offline readiness checks. Never send an LLM or Telegram request."""
import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from app.config import load_settings
from app.database.db import Database
from app.stickers.retriever import LocalSemanticEmbeddingProvider


async def vision() -> int:
    settings = load_settings(); checks: list[tuple[str, bool, str]] = []
    try:
        import PIL, lottie  # noqa: F401
        checks.append(("Pillow and lottie", True, "available"))
    except Exception as exc: checks.append(("Pillow and lottie", False, str(exc)))
    for command in ("ffmpeg", "ffprobe"):
        checks.append((command, bool(shutil.which(command)), shutil.which(command) or "not found"))
    cache = Path(settings.media_cache_dir)
    try:
        cache.mkdir(parents=True, exist_ok=True); probe = cache / ".write-test"; probe.write_text("ok"); probe.unlink()
        checks.append(("media cache", True, str(cache)))
    except Exception as exc: checks.append(("media cache", False, str(exc)))
    db = Database(settings.database_url)
    try:
        await db.connect(); version = (await db.fetchone("SELECT MAX(version) AS version FROM schema_migrations"))["version"]
        checks.append(("SQLite migrations", version == len(__import__("app.database.db", fromlist=["MIGRATIONS"]).MIGRATIONS), f"version={version}"))
    except Exception as exc: checks.append(("SQLite migrations", False, str(exc)))
    finally: await db.close()
    embedding = LocalSemanticEmbeddingProvider(settings.sticker_embedding_model, settings.sticker_embedding_device)
    try:
        await embedding.ensure_loaded(); checks.append(("embedding model", True, settings.sticker_embedding_model))
    except Exception as exc: checks.append(("embedding model", False, str(exc)))
    checks += [("vision flags", True, f"vision={settings.llm_supports_vision}; multiple_images={settings.llm_supports_multiple_images}"), ("LLM selection", True, f"provider={settings.llm_provider}; model={settings.llm_model}")]
    for name, ok, detail in checks: print(f"{'OK' if ok else 'FAIL'} {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["vision"])
    args = parser.parse_args()
    if args.command == "vision": raise SystemExit(asyncio.run(vision()))


if __name__ == "__main__": main()
