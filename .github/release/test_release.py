import unittest

from release import Error, Pull, Tag, bump, classify, highest_stable, merge_labels, plan, publish


def merged(number, sha, label):
    return Pull(number, sha, "https://example.com/pull/" + str(number), label, "notes")


class GitHub:
    def __init__(self):
        self.tags = {}
        self.releases = {}
        self.fail_release = False

    def tag(self, name):
        return self.tags.get(name)

    def release(self, name):
        return self.releases.get(name)

    def create_tag(self, entry):
        self.tags[entry.version] = Tag(entry.pull.sha, entry.marker)

    def create_release(self, entry):
        if self.fail_release:
            raise Error("release unavailable")
        self.releases[entry.version] = entry.body


class ReleaseTests(unittest.TestCase):
    def test_validation_requires_exactly_one_release_label(self):
        self.assertEqual(classify(["bug", "release: minor"]), "minor")
        for labels in ([], ["release: major", "release: none"]):
            with self.subTest(labels=labels), self.assertRaises(Error):
                classify(labels)

    def test_versions_use_numeric_stable_semver(self):
        self.assertEqual(highest_stable(["v1.9.9", "v1.10.0", "v2.0.0-rc.1", "other"]), "v1.10.0")
        for label, expected in (("major", "v2.0.0"), ("minor", "v1.5.0"), ("patch", "v1.4.3")):
            with self.subTest(label=label):
                self.assertEqual(bump("v1.4.2", label), expected)
        with self.assertRaises(Error):
            highest_stable(["v01.1.0", "v2.0.0-rc.1"])

    def test_post_merge_label_changes_do_not_change_none(self):
        events = [
            {"event": "labeled", "label": {"name": "release: none"}, "created_at": "2026-01-01T00:00:01Z"},
            {"event": "merged", "commit_id": "a", "created_at": "2026-01-01T00:00:02Z"},
            {"event": "unlabeled", "label": {"name": "release: none"}, "created_at": "2026-01-01T00:00:03Z"},
            {"event": "labeled", "label": {"name": "release: major"}, "created_at": "2026-01-01T00:00:04Z"},
        ]
        self.assertEqual(merge_labels(events, events[1]["created_at"]), "none")
        events[0]["created_at"] = events[1]["created_at"]
        with self.assertRaisesRegex(Error, "same second"):
            merge_labels(events, events[1]["created_at"])

    def test_one_run_catches_up_in_order_with_none_and_direct_push(self):
        entries = plan("v1.0.0", [merged(8, "a", "patch"), None, merged(2, "b", "none"), merged(6, "c", "minor"), merged(7, "d", "patch")])
        self.assertEqual([(entry.version, entry.pull.sha) for entry in entries], [
            ("v1.0.1", "a"), ("v1.1.0", "c"), ("v1.1.1", "d"),
        ])

    def test_tag_only_failure_finishes_before_later_release(self):
        github = GitHub()
        entries = plan("v1.0.0", [merged(1, "a", "minor"), merged(2, "b", "patch")])
        github.fail_release = True
        with self.assertRaisesRegex(Error, "unavailable"):
            publish(github, entries, set())
        self.assertEqual(list(github.tags), ["v1.1.0"])
        github.fail_release = False
        publish(github, entries, set(github.tags))
        self.assertEqual(list(github.releases), ["v1.1.0", "v1.1.1"])
        self.assertIn("https://example.com/pull/1", github.releases["v1.1.0"])
        publish(github, entries, set(github.tags))

    def test_conflicting_tag_or_unexpected_release_blocks_publication(self):
        github = GitHub()
        entries = plan("v1.0.0", [merged(1, "a", "patch")])
        github.tags["v1.0.1"] = Tag("other", entries[0].marker)
        with self.assertRaisesRegex(Error, "conflicts"):
            publish(github, entries, set(github.tags))
        self.assertFalse(github.releases)
        with self.assertRaisesRegex(Error, "Unexpected"):
            publish(GitHub(), entries, {"v1.0.2"})

    def test_later_tag_cannot_exist_ahead_of_missing_release(self):
        github = GitHub()
        entries = plan("v1.0.0", [merged(1, "a", "patch"), merged(2, "b", "patch")])
        github.create_tag(entries[1])
        with self.assertRaisesRegex(Error, "incomplete publication"):
            publish(github, entries, set(github.tags))
        self.assertNotIn("v1.0.1", github.tags)

    def test_release_with_conflicting_provenance_is_rejected(self):
        github = GitHub()
        entries = plan("v1.0.0", [merged(1, "a", "patch")])
        github.create_tag(entries[0])
        github.releases["v1.0.1"] = "unrelated release"
        with self.assertRaisesRegex(Error, "Release v1.0.1 conflicts"):
            publish(github, entries, set(github.tags))


class TimelineGitHub:
    repo = "owner/project"

    def __init__(self, events):
        self.events = events

    def pages(self, path):
        if path == "/commits/a/pulls":
            return [{"number": 1, "base": {"ref": "main"}}]
        if path == "/issues/1/timeline":
            return self.events
        raise AssertionError(path)

    def get(self, path):
        if path != "/pulls/1":
            raise AssertionError(path)
        return {
            "number": 1, "merged": True, "base": {"ref": "main"},
            "merge_commit_sha": "a", "merged_at": "2026-01-01T00:00:02Z",
            "body": "## Release notes\nKeep this\n## Other\nIgnore this",
        }


class ReplayTests(unittest.TestCase):
    def test_invalid_merge_blocks_until_classification_is_corrected(self):
        from release import pull_for

        github = TimelineGitHub([{"event": "merged", "commit_id": "a", "created_at": "2026-01-01T00:00:02Z"}])
        with self.assertRaisesRegex(Error, "PR #1.*exactly one"):
            pull_for(github, "a", set(), {})
        pull = pull_for(github, "a", set(), {"1:a": "patch"})
        self.assertEqual((pull.kind, pull.notes), ("patch", "Keep this"))

    def test_correction_cannot_replace_evidence_of_a_merge(self):
        from release import pull_for

        with self.assertRaisesRegex(Error, "does not verify the merge"):
            pull_for(TimelineGitHub([]), "a", set(), {"1:a": "patch"})
