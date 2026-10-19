# Social cognition — progress

## 2026-10-19 — Stage 1 complete: source and experience boundary

- Added `app.social` with one source adapter contract and a deterministic
  `OfflineFixtureAdapter`; no live API, scraping or outbound action was added.
- Added `SourcePermissionPolicy`. It is default-deny, separates source access
  from AI/memory/retention permission, and requires thread-specific explicit
  consent for Telegram material.
- Added normalized `SocialEvent`/`SourceDescriptor` contracts with platform,
  account, source, actor, thread, media/reply references, visibility,
  provenance, UTC timestamps and an idempotency key.
- Added consent-gated SQLite source/event records with cursor recovery,
  deduplication, rejection audit, bounded optional excerpts and deletion
  propagation. Rejected material retains no content hash or excerpt.
- The ingestor only normalizes, policy-checks and persists observations. It
  makes no LLM call and does not yet write memory, beliefs, relationships or
  personality state.
- Offline coverage verifies deduplication/cursor behavior, rejection,
  Telegram consent scope, deletion propagation and no identity inference.

**Verification:** targeted 24 passed in 1.45 s; full 382 passed in 11.41 s;
`compileall` and `git diff --check` passed.

**Next:** Stage 2 — route accepted observations into separate memory tiers with
retention/forgetting and source deletion propagation, still offline-first.

## 2026-10-19 — Stage 2 foundation: consented memory routing

- Added independent `social_memories` records for `working`, `episodic` and
  `social` tiers. They are not mixed with the existing private-chat memory
  tables until later context/privacy routing work.
- A deterministic router gives every memory-permitted event short-lived
  working context. Only direct/referential high-salience observations gain an
  episodic record; an actor reference creates a separately scoped social
  record. No semantic fact, biography item or belief is inferred at this step.
- Working/social records expire, while source deletion clears summaries and
  marks dependent social memories deleted without erasing audit provenance.
- Permitted edit events refresh the retained source snapshot. A restore needs
  fresh permitted content; it creates new experience memory instead of silently
  reviving content or memories removed by a deletion request.
- The router remains optional in the ingestor and makes no LLM call.

**Verification:** targeted 13 passed in 0.51 s; full 384 passed in 10.70 s;
`compileall` and `git diff --check` passed.

## 2026-10-19 — Stage 2 complete: privacy-scoped retrieval

- Added a read-only `SocialMemoryRetrieval` path to `ContextBuilder`. It uses
  the exact opaque `consent_scope` of the current chat (`chat:<id>`), so a
  permitted observation for one conversation cannot enter another one.
- Retrieval excludes revoked/expired sources, deleted/expired memory, and
  deleted source events. It selects at most one representation of an event
  even if that event has working, episodic and social tier records.
- External observations enter the model context only as a compact explicitly
  untrusted block: they are neither instructions, established facts nor Anya's
  opinions. This is intentionally separate from private chat memory.
- The application wires the retrieval object but has no live source adapter;
  therefore this adds no network work, LLM call, Telegram action or external
  content unless a future explicit, consented adapter creates matching records.

**Verification:** targeted 23 passed in 0.85 s; full 385 passed in 10.76 s;
`compileall` and `git diff --check` passed.

**Next:** Stage 3 — add evidence records and a conservative, provenance-aware
belief revision boundary. No source statement will become an Anya belief by
default.

## 2026-10-19 — Stage 3 foundation: evidence and revisable views

- Retrieval now excludes expired social memory in its SQL predicate even if a
  background expiry job has not run. Telegram ingestion now fails closed when
  a source event lacks a thread ID or its thread differs from the consent
  scope.
- Added separate provenance-carrying `social_evidence`, `social_beliefs`,
  evidence-link and revision tables. Existing private memories, episodes,
  relationship state and developed-personality records remain untouched.
- An `external_claim` is stored only as evidence. It cannot form or revise an
  Anya belief. Formation requires an explicit own-reflection/curated proposal;
  revision accepts only qualified evidence and deduplicates by an independent
  source key, so repetition/popularity cannot amplify confidence.
- Relevant active positions can enter `ContextBuilder` as compact, explicitly
  revisable views. Raw external evidence is never inserted into that block.
- When a source deletes an event, linked evidence is retracted and its prior
  confidence contribution is reversed through a separate revision record.
  The original audit/provenance row remains intact.
- This foundation runs no appraisal automatically and makes no extra LLM,
  Telegram or network call. Topic extraction and evidence qualification remain
  explicit future work rather than silently inferred from a post.

**Verification:** targeted 28 passed in 0.98 s; full 390 passed in 11.56 s;
`compileall` and `git diff --check` passed.

**Next:** complete Stage 3 with structured offline appraisal fixtures,
contradiction handling and carefully bounded belief formation from qualified
appraisal rather than raw source text.
