"""Permission gate for social sources, before appraisal, LLM, or memory."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.social.models import SocialEvent


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str | None = None


class SourcePermissionPolicy:
    """Default-deny policy for every externally delivered observation."""

    permitted_usage_modes = frozenset({"own", "explicit_consent", "permitted_feed", "offline_fixture"})

    def evaluate(self, event: SocialEvent, *, now: datetime | None = None) -> PermissionDecision:
        permission = event.source.permission
        now = now or datetime.now(timezone.utc)
        if permission.usage_mode not in self.permitted_usage_modes:
            return PermissionDecision(False, "unsupported_usage_mode")
        if not permission.valid_now(now):
            return PermissionDecision(False, "permission_expired")
        if not permission.ai_processing_allowed:
            return PermissionDecision(False, "ai_processing_not_allowed")
        if event.source.platform == "telegram":
            # Telegram's content licensing terms require explicit, continued,
            # context-specific consent for AI processing. A broad account-level
            # switch is intentionally insufficient.
            if not permission.explicit_consent or not permission.consent_scope:
                return PermissionDecision(False, "telegram_context_consent_required")
            if not event.thread_id:
                return PermissionDecision(False, "telegram_thread_id_required")
            if permission.consent_scope != event.thread_id:
                return PermissionDecision(False, "telegram_consent_scope_mismatch")
        return PermissionDecision(True)
