# Commit changelog rule

Before every commit, update `CHANGELOG.md` with the new project version. Use a minor version for a meaningful feature and a patch version for a fix, hotfix, refactor, or regression correction. Add the entry at the top before making the commit. Keep the description concise and focused, but there is no fixed character limit.

# Test record rule

Keep `TESTING.md` current after code changes: record the actual targeted and full pytest results when they are run, including duration and the scope checked. Append a new row; do not rewrite older measurements or record secrets/private conversation content.
