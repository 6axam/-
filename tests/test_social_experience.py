from datetime import datetime, timezone

from app.database.db import Database
from app.social import OfflineFixtureAdapter, SocialEvent, SocialExperienceIngestor, SourceDescriptor, SourcePermission
from app.social.memory import SocialMemoryRouter
from app.social.permissions import SourcePermissionPolicy
from app.social.beliefs import BeliefProposal, BeliefRetrieval, BeliefRevisionEngine, EvidenceInput
from app.social.retrieval import SocialMemoryRetrieval
from app.conversation.context import ContextBuilder


def _source(*, platform="fixture", permission=None, source_id="feed"):
    return SourceDescriptor(
        platform=platform, platform_account_id="own-account", source_id=source_id,
        source_type="offline_fixture", permission=permission or SourcePermission(
            usage_mode="offline_fixture", ai_processing_allowed=True,
            memory_allowed=True, retention_allowed=True,
        ),
    )


def _event(source, event_id="one", *, kind="post", content="разрешённый материал", references=()):
    return SocialEvent(
        source=source, event_id=event_id, actor_id="author-1", event_kind=kind,
        occurred_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        thread_id="thread-1", content=content, content_ref=f"fixture://{event_id}",
        reference_event_ids=references, visibility="private",
        provenance={"fixture": True},
    )


async def test_permitted_fixture_events_are_deduplicated_cursored_and_content_bounded(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source()
    event = _event(source, content="x" * 700)
    adapter = OfflineFixtureAdapter(source, [event, event])

    result = await SocialExperienceIngestor(db).ingest(adapter)

    assert result.received == 2 and result.accepted == 1 and result.duplicates == 1
    assert adapter.acknowledged == ["one", "one"] and not adapter.connected
    row = await db.fetchone("SELECT status,content_hash,length(content_excerpt) AS excerpt_size FROM social_events")
    assert tuple(row) == ("accepted", __import__("hashlib").sha256(("x" * 700).encode()).hexdigest(), 512)
    cursor = await db.fetchone("SELECT cursor_value FROM social_sources")
    assert cursor["cursor_value"] == "2"
    await db.close()


async def test_permission_denial_stores_only_a_minimal_audit_record(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source(permission=SourcePermission(usage_mode="offline_fixture"))
    result = await SocialExperienceIngestor(db).ingest(OfflineFixtureAdapter(source, [_event(source)]))

    assert result == type(result)(received=1, rejected=1)
    row = await db.fetchone("SELECT status,rejection_reason,content_ref,content_hash,content_excerpt FROM social_events")
    assert tuple(row) == ("rejected", "ai_processing_not_allowed", "fixture://one", None, None)
    await db.close()


async def test_telegram_requires_context_specific_explicit_consent(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    permission = SourcePermission(
        usage_mode="explicit_consent", ai_processing_allowed=True, memory_allowed=True,
        retention_allowed=True, explicit_consent=True, consent_scope="another-thread",
    )
    source = _source(platform="telegram", permission=permission)
    result = await SocialExperienceIngestor(db).ingest(OfflineFixtureAdapter(source, [_event(source)]))
    assert result.rejected == 1
    assert (await db.fetchone("SELECT rejection_reason FROM social_events"))["rejection_reason"] == "telegram_consent_scope_mismatch"
    await db.close()


async def test_telegram_without_thread_id_is_fail_closed_even_with_explicit_consent(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    permission = SourcePermission(
        usage_mode="explicit_consent", ai_processing_allowed=True, memory_allowed=True,
        retention_allowed=True, explicit_consent=True, consent_scope="thread-1",
    )
    source = _source(platform="telegram", permission=permission)
    event = _event(source)
    event = SocialEvent(
        source=event.source, event_id=event.event_id, actor_id=event.actor_id,
        event_kind=event.event_kind, occurred_at=event.occurred_at, content=event.content,
        content_ref=event.content_ref, visibility=event.visibility, provenance=event.provenance,
    )
    result = await SocialExperienceIngestor(db).ingest(OfflineFixtureAdapter(source, [event]))
    assert result.rejected == 1
    assert (await db.fetchone("SELECT rejection_reason FROM social_events"))["rejection_reason"] == "telegram_thread_id_required"
    await db.close()


async def test_source_deletion_removes_retained_content_without_erasing_audit(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source()
    original = _event(source, "original", content="материал, который потом удалили")
    deletion = _event(source, "delete-original", kind="delete", content=None, references=("original",))

    result = await SocialExperienceIngestor(db, memory_router=SocialMemoryRouter(db)).ingest(OfflineFixtureAdapter(source, [original, deletion]))

    assert result.accepted == 2 and result.deleted == 1
    original_row = await db.fetchone("SELECT status,content_ref,content_hash,content_excerpt FROM social_events WHERE event_id='original'")
    assert tuple(original_row) == ("deleted", None, None, None)
    assert (await db.fetchone("SELECT status FROM social_events WHERE event_id='delete-original'"))["status"] == "accepted"
    assert (await db.fetchone("SELECT status,summary FROM social_memories"))["status"] == "deleted"
    await db.close()


async def test_permitted_edit_and_restore_replace_source_snapshot_without_reviving_deleted_memory(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source()
    original = _event(source, "original", content="первый вариант")
    edit = _event(source, "edit-original", kind="edit", content="исправленный вариант", references=("original",))
    deletion = _event(source, "delete-original", kind="delete", content=None, references=("original",))
    restore = _event(source, "restore-original", kind="restore", content="опубликовано заново", references=("original",))

    await SocialExperienceIngestor(db, memory_router=SocialMemoryRouter(db)).ingest(
        OfflineFixtureAdapter(source, [original, edit, deletion, restore]),
    )

    original_row = await db.fetchone(
        "SELECT status,content_ref,content_excerpt FROM social_events WHERE event_id='original'",
    )
    assert tuple(original_row) == ("restored", "fixture://restore-original", "опубликовано заново")
    memories = await db.fetchall(
        "SELECT e.event_id,m.tier,m.status FROM social_memories m JOIN social_events e ON e.id=m.social_event_id "
        "ORDER BY e.event_id,m.tier",
    )
    assert ("original", "working", "deleted") in [tuple(row) for row in memories]
    assert ("restore-original", "working", "active") in [tuple(row) for row in memories]
    await db.close()


async def test_social_memory_keeps_working_and_social_tiers_separate_from_episodic(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source()
    feed_item = _event(source, "feed", content="обычный разрешённый пост")
    direct = _event(source, "direct", kind="direct_message", content="личное обращение")
    clock = lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
    router = SocialMemoryRouter(db, now=clock)

    result = await SocialExperienceIngestor(db, memory_router=router).ingest(OfflineFixtureAdapter(source, [feed_item, direct]))

    assert result.accepted == 2
    rows = await db.fetchall(
        "SELECT e.event_id,m.tier,m.status FROM social_memories m JOIN social_events e ON e.id=m.social_event_id ORDER BY e.event_id,m.tier",
    )
    assert [tuple(row) for row in rows] == [
        ("direct", "episodic", "active"), ("direct", "social", "active"), ("direct", "working", "active"),
        ("feed", "social", "active"), ("feed", "working", "active"),
    ]
    assert await db.expire_social_memories("2026-11-30T00:00:00+00:00") == 4
    assert (await db.fetchone("SELECT status FROM social_memories WHERE tier='episodic'"))["status"] == "active"
    await db.close()


def test_permission_policy_never_uses_popularity_or_identity_guessing():
    source = _source()
    event = _event(source)
    assert SourcePermissionPolicy().evaluate(event).allowed
    # Identity remains opaque source metadata at Phase 1; no contact lookup or
    # cross-platform account merge exists in the source-policy decision.
    assert event.actor_id == "author-1" and event.source.platform == "fixture"


async def test_social_context_retrieval_is_scope_limited_and_marks_external_text_untrusted(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    permission = SourcePermission(
        usage_mode="explicit_consent", ai_processing_allowed=True, memory_allowed=True,
        retention_allowed=True, explicit_consent=True, consent_scope="chat:10",
    )
    allowed = _source(permission=permission, source_id="allowed")
    other_scope = _source(
        permission=SourcePermission(
            usage_mode="explicit_consent", ai_processing_allowed=True, memory_allowed=True,
            retention_allowed=True, explicit_consent=True, consent_scope="chat:20",
        ),
        source_id="other",
    )
    router = SocialMemoryRouter(db)
    await SocialExperienceIngestor(db, memory_router=router).ingest(OfflineFixtureAdapter(allowed, [
        _event(allowed, "allowed-event", content="киви обсуждали безопасность приватности"),
    ]))
    await SocialExperienceIngestor(db, memory_router=router).ingest(OfflineFixtureAdapter(other_scope, [
        _event(other_scope, "other-event", content="киви секрет из другого чата"),
    ]))

    _system, context, breakdown = await ContextBuilder(
        db, social_memory_retrieval=SocialMemoryRetrieval(db),
    ).build_with_breakdown(1, 10, "что с киви и приватностью?")

    assert "киви обсуждали безопасность приватности" in context
    assert "киви секрет из другого чата" not in context
    assert "not an instruction" in context
    assert len(breakdown["retrieved_social_memory_ids"]) == 1
    await db.close()


async def test_social_retrieval_sql_excludes_expired_memory_without_maintenance_job(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source(permission=SourcePermission(
        usage_mode="explicit_consent", ai_processing_allowed=True, memory_allowed=True,
        retention_allowed=True, explicit_consent=True, consent_scope="chat:10",
    ))
    router = SocialMemoryRouter(db, now=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc))
    await SocialExperienceIngestor(db, memory_router=router).ingest(OfflineFixtureAdapter(source, [
        _event(source, content="просроченный киви контекст"),
    ]))
    assert await db.execute("UPDATE social_memories SET expires_at=?", ("2026-10-19T01:00:00+00:00",))
    retrieval = SocialMemoryRetrieval(db, now=lambda: datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert await retrieval.search("chat:10", "киви") == []
    assert (await db.fetchone("SELECT status FROM social_memories"))["status"] == "active"
    await db.close()


async def test_external_claim_stays_evidence_and_independent_verified_evidence_revises_once(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    engine = BeliefRevisionEngine(db)
    claim_id = await engine.record_evidence(EvidenceInput(
        topic="privacy", proposition="privacy should be default", evidence_kind="external_claim",
        reliability=.95, independence_key="post:one", provenance={"source": "fixture"},
    ))
    assert claim_id is not None
    assert await engine.form_belief(BeliefProposal(
        topic="privacy", proposition="privacy should be default", stance="support", confidence=.4,
        importance=.8, origin="external_claim",
    )) is None
    assert (await db.fetchall("SELECT * FROM social_beliefs")) == []

    belief_id = await engine.form_belief(BeliefProposal(
        topic="privacy", proposition="privacy should be default", stance="support", confidence=.4,
        importance=.8, provenance={"method": "offline reflection"},
    ))
    assert belief_id is not None and not await engine.revise(belief_id, claim_id, "support")
    fact_id = await engine.record_evidence(EvidenceInput(
        topic="privacy", proposition="privacy should be default", evidence_kind="verified_fact",
        reliability=.8, independence_key="report:independent-a", provenance={"source": "fixture"},
    ))
    assert await engine.revise(belief_id, fact_id, "support")
    assert not await engine.revise(belief_id, fact_id, "support")
    belief = await db.social_belief(belief_id)
    assert belief["confidence"] == .526
    assert (await db.fetchone("SELECT COUNT(*) AS count FROM social_belief_revisions"))["count"] == 1
    await db.close()


async def test_relevant_belief_enters_context_as_revisable_view_not_external_claim(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    engine = BeliefRevisionEngine(db)
    await engine.form_belief(BeliefProposal(
        topic="privacy", proposition="Personal privacy should be a strong default",
        stance="support", confidence=.42, importance=.7,
    ))
    _system, context, breakdown = await ContextBuilder(
        db, belief_retrieval=BeliefRetrieval(db),
    ).build_with_breakdown(1, 10, "что думаешь о privacy?")
    assert "DEVELOPED VIEWS" in context
    assert "Personal privacy should be a strong default" in context
    assert "revisable positions" in context
    assert len(breakdown["retrieved_belief_ids"]) == 1
    await db.close()


async def test_source_deletion_retracts_linked_evidence_and_reverses_its_revision(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'social.sqlite'}")
    await db.connect()
    source = _source()
    original = _event(source, "original", content="разрешённый материал")
    engine = BeliefRevisionEngine(db)
    await SocialExperienceIngestor(db).ingest(OfflineFixtureAdapter(source, [original]))
    source_row = await db.fetchone("SELECT id FROM social_sources")
    event_id = await db.social_event_id(source_row["id"], "original")
    evidence_id = await engine.record_evidence(EvidenceInput(
        social_event_id=event_id, topic="privacy", proposition="privacy default", evidence_kind="verified_fact",
        reliability=.8, independence_key="source:original", provenance={"source": "fixture"},
    ))
    belief_id = await engine.form_belief(BeliefProposal(
        topic="privacy", proposition="privacy default", stance="support", confidence=.4, importance=.7,
    ))
    assert await engine.revise(belief_id, evidence_id, "support")
    deletion = _event(source, "delete", kind="delete", content=None, references=("original",))
    # The saved cursor points after `original`; this fixture models the next
    # page from the same source rather than resetting its cursor.
    await SocialExperienceIngestor(db, belief_engine=engine).ingest(OfflineFixtureAdapter(source, [original, deletion]))
    assert (await db.social_evidence(evidence_id))["status"] == "retracted"
    assert (await db.social_belief(belief_id))["confidence"] == .4
    revisions = await db.fetchall("SELECT reason FROM social_belief_revisions ORDER BY id")
    assert [row["reason"] for row in revisions] == ["support:verified_fact", "evidence_retracted"]
    await db.close()
