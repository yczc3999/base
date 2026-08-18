# Anonymous GitHub campaign fixtures

This directory contains normalized, credential-free GitHub API evidence used by
the Base upgrade dispatch tests. It is not a recording of a real organization,
repository, workflow run, pull request, token, or artifact.

`campaign.json` models one approved anonymous campaign with four registry
outcomes:

1. `fixture_alpha` was dispatched and its receiver opened a Draft PR;
2. `fixture_bravo` was already current, so it appears in the campaign batch but
   has no dispatch evidence entry;
3. `fixture_charlie` was dispatched and returned `verification_failed` without
   publishing a branch or PR;
4. `fixture_delta` was dispatched and received a run identity, but polling
   timed out before an artifact or terminal receiver result could be collected.

Only actual receiver runs belong in the evidence wrapper. Therefore its
`entries` array contains alpha, charlie, and delta, while an all-current or
pre-dispatch blocked campaign may validly use an empty array. A run is appended
as soon as dispatch returns its identity. On a later provider or collection
failure, unavailable completion, artifact, result hash, and final-status fields
remain null and `failure_stage` records the failed operation. Dispatch tests may
use each entry to mock the fixed-200 dispatch run-details response, run polling,
artifact metadata/download, and result verification. The dispatch request does
not send a `return_run_details` compatibility flag. Hashes, run IDs, timestamps,
repositories, and URLs are synthetic and deterministic.

Production evidence deliberately omits a separate repository field, but its
GitHub run URL still reveals `OWNER/REPO`. It must be written only to the private
operator/ops repository or its protected CI artifacts; it never belongs in Base.
The top-level `operator_commit` binds the evidence to the exact 40-hex Base
checkout commit that executed the campaign, including when the managed
`BASE_PLATFORM_REF` points to an immutable release tag.
Provider credentials remain in
`BASE_UPGRADE_GITHUB_TOKEN` and must not appear in registry, evidence, logs, or
fixtures.
