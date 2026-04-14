# 0003: Production readiness assessment design

## Context

TST surfaces tools by popularity (stars) and personal experience (status). For enterprise and regulated use cases, this creates false confidence — a tool with 10k GitHub stars may have no releases in 2 years, no CI, an AGPL license, and known CVEs. Users need honest signals about production fitness.

Three key design decisions were made:

## Decision 1: Separate `production_assessments` table

Assessments are stored in their own table with a UNIQUE(tool_id) constraint, not as inline columns on the `tools` table. Reasons:

- Assessments are temporal snapshots that expire — they're not intrinsic tool properties
- The `tools` table already has 30 columns; adding 15 more would make it unwieldy
- UNIQUE(tool_id) + INSERT OR REPLACE gives clean "always latest" semantics
- ON DELETE CASCADE keeps things tidy when tools are removed

## Decision 2: Transparent weighted-average scoring

The overall score (0.0–1.0) uses a simple weighted average of 9 signals, with unknowns excluded from both numerator and denominator. This was chosen over an opaque ML model or complex formula because:

- Users in regulated environments need to explain their tool choices — a transparent formula is auditable
- Each signal's weight is visible in the code (20% commit recency, 15% contributors, etc.)
- The honest framing principle: if we can't explain the score, we shouldn't show it

## Decision 3: Honest framing is the feature

The assessment module exists not because TST can fully solve production fitness evaluation (it can't — you need humans for regulated software selection), but because flagging the gap clearly is better than silently surfacing weak recommendations. Every assessment output includes a disclaimer: "This does NOT constitute a security audit or compliance certification."

## Consequences

- Every assessment requires GitHub API calls (3 concurrent) — rate limits matter when assessing many tools at once
- OSV.dev CVE checks are best-effort (package name heuristic from repo name) — may miss or false-positive
- Non-GitHub-repo tools get a minimal `non_repo` report — the module is honest about what it can't assess
- The scoring weights are subjective — but they're transparent and can be adjusted based on user feedback
