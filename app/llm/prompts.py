from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def read_prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")

def response_rules() -> str:
    return read_prompt("response.md")
