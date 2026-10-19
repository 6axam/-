# Social cognition — staged plan

## Invariants

- One Anya world, contact model, event journal and conversation pipeline; a
  platform adapter never creates a platform-specific personality.
- A source connection is not consent. `SourcePermissionPolicy` runs before
  LLM appraisal, memory, belief, relationship, or context use.
- Source records retain provenance, cursor and deletion state. Full third-party
  text is not stored by default; a bounded excerpt is retained only when the
  source explicitly permits retention.
- No cross-platform identity merge occurs from names, writing style or inferred
  similarity. A future confirmed-link flow must be explicit and auditable.
- Real Telegram, RSS, Reddit and X ingestion stays disabled until a dedicated
  adapter and policy configuration are reviewed. Tests use offline fixtures.

## Stages

1. **Source and experience boundary** — normalized event contract, source
   permissions, cursoring, deduplication, edits/deletions and offline fixture.
2. **Memory routing** — working/episodic/semantic/autobiographical/social
   separation, retention and source-deletion propagation.
3. **Belief and opinion engine** — evidence, uncertainty, independent-source
   weighting and revision; external claims never become Anya's belief directly.
4. **Values and social influence** — bounded moral reflection, value inertia,
   relationship-aware but non-controlling influence.
5. **Interest, attention and consolidation** — local relevance filter, budgets,
   decay, cooldowns and limited appraisal/consolidation work.
6. **Decision/context integration** — retrieve relevant beliefs and experience
   for normal replies and safely gated initiative.
7. **Permitted adapters and Monitor** — RSS first, then only approved platform
   adapters; read-only observability and offline simulator scenarios.

## Compliance boundary

Telegram's content licensing terms prohibit using platform data for AI unless
the relevant users give explicit, informed, continuing, context-specific
consent. The policy therefore requires consent scoped to the exact Telegram
thread before any AI processing. Reddit/X adapters remain disabled until their
official API terms, applicable data permissions and use purpose are configured
for the exact integration. No scraping is part of this project.

Reference policies: [Telegram content licensing](https://telegram.org/tos/content-licensing),
[Reddit Developer Terms](https://redditinc.com/policies/developer-terms), and
[Reddit Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy).
