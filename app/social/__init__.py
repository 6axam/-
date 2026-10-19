"""Consent-gated external social experience primitives.

Adapters only deliver normalized observations. They never modify Anya's
personality, memories, relationships, or beliefs directly.
"""

from app.social.adapters import OfflineFixtureAdapter, SocialSourceAdapter
from app.social.experience import SocialExperienceIngestor
from app.social.memory import SocialMemoryRouter
from app.social.models import SocialEvent, SourceDescriptor, SourcePermission
from app.social.permissions import SourcePermissionPolicy
from app.social.retrieval import SocialMemoryRetrieval

__all__ = (
    "OfflineFixtureAdapter", "SocialEvent", "SocialExperienceIngestor",
    "SocialMemoryRetrieval", "SocialMemoryRouter", "SocialSourceAdapter", "SourceDescriptor", "SourcePermission",
    "SourcePermissionPolicy",
)
