# Running the release workflow

The maintainer merges PRs and chooses the final release classification. GitHub Actions replays eligible merges in first-parent order, using one release classification per PR. Use squash or merge commits. The workflow depends on GitHub reporting the exact PR merge result. A multi-commit rebase introduces commits without individual merge records and blocks replay. A single-commit rebase can look like a squash in the API. Keep rebase merges disabled after rollout if you need to enforce the supported merge methods in GitHub.

## Activate once

1. Run the disposable-repository checks below. Create the four labels listed in the README and give the rollout PR exactly one release label. Review it just before merging, and wait at least a few seconds after the last release-label change before merging. GitHub records these events to the second, so a label change in the same second as the merge blocks replay.
2. Before merging the rollout PR, set the repository Actions variable `RELEASE_BASE_TAG` to the highest existing stable tag, such as `v1.0.0`. The tag must peel to a first-parent commit on `main`. Keep pre-activation stable tags at or before that baseline.
3. Immediately before merging the rollout PR, set the repository Actions variable `RELEASE_AFTER_SHA` to the full SHA of the current tip of `main`. This is the exclusive replay boundary. If `main` advances before rollout, update the variable to the new tip before merging. Keep both anchors fixed once the rollout PR merges.
4. Merge the rollout PR using squash or a merge commit. It is the first eligible PR. For a release-producing label, confirm that **Publish releases** tags the rollout merge-result SHA and publishes a release with a link to the PR.
5. Add **Release classification** as a required check on `main` once the check has appeared in Actions. If required checks are available during rollout, you can require it before the rollout merge. The publisher also enforces classification when branch checks are bypassed.

Leave label names stable so historical timeline events continue to be meaningful. Configure immutable releases and a ruleset preventing updates or deletions of version tags if available. The publishing job must retain permission to create new tags. The workflow leaves policy settings to the maintainer.

## Recover a stopped run

Open the failed **Publish releases** run. Its log identifies the version, PR, or commit that stopped progress. Rerun the failed job after fixing the prerequisite. Replay starts from activation each time. A matching tag with an absent release is completed before later versions can publish. A conflicting tag, release, or unexplained history entry needs maintainer investigation. Never move a tag to resume publication.

- A blocked merge with an invalid or ambiguous merge-time label can be corrected through the repository Actions variable `RELEASE_CORRECTIONS`. Use a JSON object such as `{"42:0123456789abcdef0123456789abcdef01234567":"patch"}` keyed by PR number and full merge-result SHA. Record the reason and chosen classification on the issue or PR before setting the value. Use this only before a release tag exists for that PR. Keep the entry for future replays. Editing a PR label after merge does not change replay.
- If the workflow encounters an intentional direct push, verify it is not the merge result of a PR. Record the reason and commit in the project, then add the full SHA to the space-separated `RELEASE_DIRECT_PUSH_SHAS` repository variable. This excludes that commit from publication. A missing PR match alone is insufficient evidence of a direct push.
- If GitHub temporarily fails to report a merge just after it happened, rerun after verifying the association and PR timeline are available. If the merge method is unsupported or the timeline cannot verify the merge itself, resolve the history inconsistency before resuming. A classification correction applies only to labels.

## Verify on GitHub before activating

Local checks run with `make test`. Use a disposable private repository for the following GitHub checks. Verify GitHub's current event and API behavior before enabling release automation on a consumer-facing repository.

1. Seed a stable `v1.0.0` tag on `main` and configure the activation variables and labels. Introduce the workflows in a rollout PR. Confirm that the classification check fails without a label, passes after adding `release: patch`, fails on a second label, and passes after removing the extra one. Verify the required check if the test repository supports it. Merge the rollout PR and confirm that the publisher creates `v1.0.1` on its merge-result SHA.
2. Close an unmerged PR and push a test commit to `main` directly. Neither should publish. Record the known direct-push SHA if it is after activation. Merge a labeled `release: none` PR and relabel it after merge. Confirm it remains skipped.
3. Merge three classified PRs quickly, using both squash and merge commits. Verify their version ordering even if Actions replaces a pending run. Use `git rev-parse 'vX.Y.Z^{commit}'` for each tag and compare with the merge SHA shown by the PR API. Check the published release notes and PR links.
4. In the disposable workflow only, add a one-time failure immediately after tag creation and before release creation. Confirm the run leaves a tag without a release. Remove the failure for a subsequent merged revision. Confirm replay completes the existing release before publishing any later version and leaves the tag identity intact.
5. Bypass the required check in the disposable repository and merge a PR with invalid classification. Confirm the release job reports that PR and blocks later PRs. Record a correction in the repository variable and rerun. Try a label edit in the same second as merge or use a fixture when timing cannot be reproduced.
6. Compare two scratch installations pinned to the baseline, one before and one after publication. The installed files must match.

Record the run links and any unavailable checks in the implementation PR.
