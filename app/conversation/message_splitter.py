import re

from app.actions.models import Action, ActionType


class MessageSplitter:
    """Conservative conversational fallback for a single long text action."""
    transition = re.compile(r"\s+(?=(?:а|но|хотя|кстати|короче|ещё|вообще)\b)", re.IGNORECASE)
    sentence = re.compile(r"(?<=[.!?])\s+")
    urls = re.compile(r"https?://|www\.", re.IGNORECASE)
    paths = re.compile(r"(?:^|\s)(?:~?/|\./|\.\./)[\w./-]+")
    shell_prefixes = ("$ ", "git ", "python", "pip ", "npm ", "curl ", "docker ", "cd ", "./")

    def __init__(self, *, enabled: bool = True, target_chars: int = 70, min_chars: int = 25, max_parts: int = 4):
        self.enabled = enabled
        self.target_chars = target_chars
        self.min_chars = min_chars
        self.max_parts = max_parts

    def split_actions(self, actions: list[Action]) -> list[Action]:
        texts = [action for action in actions if action.type == ActionType.text]
        if not self.enabled or len(texts) != 1:
            return actions
        target = texts[0]
        parts = self.split_text(target.text or "")
        if len(parts) == 1:
            return actions
        result = []
        for action in actions:
            if action is target:
                result.extend(Action(
                    type=ActionType.text, text=part,
                    reply_to_message_id=action.reply_to_message_id,
                    automatic=action.automatic,
                    cancelable=action.cancelable,
                    priority=action.priority,
                ) for part in parts)
            else:
                result.append(action)
        return result

    def split_text(self, text: str) -> list[str]:
        text = text.strip()
        if len(text) <= self.target_chars or self._technical(text):
            return [text]
        pieces = []
        for sentence in self.sentence.split(text):
            pieces.extend(self._dash_parts(sentence))
        refined = []
        for piece in pieces:
            refined.extend(self._transition_parts(piece))
        parts = self._merge_tiny_parts([part.strip() for part in refined if part.strip()])
        if len(parts) < 2:
            return [text]
        if len(parts) > self.max_parts:
            parts = parts[: self.max_parts - 1] + [" ".join(parts[self.max_parts - 1:])]
        return parts

    def _dash_parts(self, text: str) -> list[str]:
        if " — " not in text:
            return [text]
        left, right = text.split(" — ", 1)
        if len(left.strip()) >= self.min_chars and len(right.strip()) >= self.min_chars:
            return [left, right]
        return [text]

    def _transition_parts(self, text: str) -> list[str]:
        for match in self.transition.finditer(text):
            left, right = text[:match.start()].strip(), text[match.end():].strip()
            if len(left) >= self.min_chars and len(right) >= self.min_chars:
                return [left, right]
        return [text]

    def _merge_tiny_parts(self, parts: list[str]) -> list[str]:
        merged = []
        for part in parts:
            if merged and len(part) < self.min_chars:
                merged[-1] += " " + part
            else:
                merged.append(part)
        return merged

    def _technical(self, text: str) -> bool:
        stripped = text.strip()
        return (
            "```" in text or "`" in text or "\n" in text or self.urls.search(text) is not None
            or self.paths.search(text) is not None or stripped.startswith(("{", "["))
            or stripped.lower().startswith(self.shell_prefixes) or "Traceback" in text or "File \"" in text
        )
