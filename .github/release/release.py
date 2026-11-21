"""Validate release labels and replay merged PRs from the configured boundary."""

from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LABELS = {f"release: {kind}": kind for kind in ("major", "minor", "patch", "none")}
STABLE = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA = re.compile(r"[0-9a-f]{40}")
NOTES = re.compile(r"^## Release notes[ \t]*\r?\n(.*?)(?=^## |\Z)", re.M | re.S)
MARKER = "agent-workflows-release "


class Error(Exception):
    pass


class Transient(Error):
    pass


def classify(labels):
    selected = sorted({name for name in labels if name in LABELS})
    if len(selected) != 1:
        raise Error(f"expected exactly one release label, found {selected}")
    return LABELS[selected[0]]


def version_key(name):
    match = STABLE.fullmatch(name)
    return tuple(map(int, match.groups())) if match else None


def highest_stable(names):
    stable = [name for name in names if version_key(name) is not None]
    if not stable:
        raise Error("no stable version tag found")
    return max(stable, key=version_key)


def bump(version, kind):
    major, minor, patch = version_key(version)
    next_version = {
        "major": (major + 1, 0, 0),
        "minor": (major, minor + 1, 0),
        "patch": (major, minor, patch + 1),
    }[kind]
    return "v" + ".".join(map(str, next_version))


def verify_merge(events, sha, merged_at):
    merges = [event for event in events if event["event"] == "merged"]
    if len(merges) != 1 or merges[0].get("commit_id") != sha or merges[0].get("created_at") != merged_at:
        raise Error("timeline does not verify the merge SHA and time")


def merge_labels(events, merged_at):
    selected = set()
    for event in events:
        if event["event"] not in ("labeled", "unlabeled"):
            continue
        name = event["label"]["name"]
        if name not in LABELS:
            continue
        date = event["created_at"]
        if date == merged_at:
            raise Error("release label changed in the same second as the merge")
        if date < merged_at:
            if event["event"] == "labeled":
                selected.add(name)
            else:
                selected.discard(name)
    return classify(selected)


@dataclass(frozen=True)
class Pull:
    number: int
    sha: str
    url: str
    kind: str
    notes: str


@dataclass(frozen=True)
class Tag:
    sha: str
    message: str


@dataclass(frozen=True)
class Entry:
    version: str
    pull: Pull

    @property
    def marker(self):
        data = {"pr": self.pull.number, "sha": self.pull.sha, "bump": self.pull.kind, "version": self.version}
        return MARKER + json.dumps(data, sort_keys=True, separators=(",", ":"))

    @property
    def body(self):
        return f"<!-- {self.marker} -->\n\nSource PR: {self.pull.url}\n\n{self.pull.notes}".rstrip()


def plan(base, pulls):
    entries = []
    for pull in pulls:
        if pull is not None and pull.kind != "none":
            base = bump(base, pull.kind)
            entries.append(Entry(base, pull))
    return entries


def check_state(github, entry):
    tag = github.tag(entry.version)
    release = github.release(entry.version)
    if tag is not None and tag != Tag(entry.pull.sha, entry.marker):
        raise Error(f"Tag {entry.version} conflicts with PR #{entry.pull.number}")
    if release is not None:
        if tag is None or release.splitlines()[:1] != [entry.body.splitlines()[0]]:
            raise Error(f"Release {entry.version} conflicts with PR #{entry.pull.number}")
    return tag is not None, release is not None


def retry(operation):
    for attempt in range(3):
        try:
            return operation()
        except Transient:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def publish(github, entries, existing_versions):
    unexpected = existing_versions - {entry.version for entry in entries}
    if unexpected:
        raise Error(f"Unexpected stable versions: {', '.join(sorted(unexpected))}")
    pending = False
    unfinished = []
    for entry in entries:
        tagged, released = retry(lambda: check_state(github, entry))
        if pending and tagged:
            raise Error(f"Tag {entry.version} exists after an incomplete publication")
        pending = not released
        if pending:
            unfinished.append(entry)

    for entry in unfinished:
        def ensure_tag():
            tagged, _ = check_state(github, entry)
            if not tagged:
                github.create_tag(entry)
                if not check_state(github, entry)[0]:
                    raise Transient(f"Tag {entry.version} is not visible yet")

        def ensure_release():
            _, released = check_state(github, entry)
            if not released:
                github.create_release(entry)
                if not check_state(github, entry)[1]:
                    raise Transient(f"Release {entry.version} is not visible yet")

        retry(ensure_tag)
        retry(ensure_release)


class GitHub:
    def __init__(self, repo, token):
        self.url = f"https://api.github.com/repos/{repo}"
        self.repo = repo
        self.token = token

    def request(self, path, data=None, missing=False):
        request = Request(
            self.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response), response.headers.get("Link", "")
        except HTTPError as error:
            if missing and error.code == 404:
                return None, ""
            transient = error.code in (408, 429, 500, 502, 503, 504)
            if error.code == 403 and (error.headers.get("Retry-After") or error.headers.get("X-RateLimit-Remaining") == "0"):
                transient = True
            cls = Transient if transient else Error
            raise cls(f"GitHub HTTP {error.code} for {request.get_method()} {path}") from None
        except (URLError, TimeoutError, OSError) as error:
            raise Transient(f"GitHub connection failed for {request.get_method()} {path}: {error}") from None

    def get(self, path, missing=False):
        return retry(lambda: self.request(path, missing=missing)[0])

    def pages(self, path):
        page = 1
        while True:
            query = urlencode({"per_page": 100, "page": page})
            items, links = retry(lambda: self.request(f"{path}?{query}"))
            if not isinstance(items, list):
                raise Error(f"GitHub did not return a list for {path}")
            yield from items
            if 'rel="next"' not in links:
                return
            page += 1

    def tag(self, version):
        ref = self.request(f"/git/ref/tags/{version}", missing=True)[0]
        if ref is None:
            return None
        target = ref["object"]
        if target["type"] != "tag":
            return Tag(target["sha"], "")
        obj = self.request(f"/git/tags/{target['sha']}")[0]
        sha = obj["object"]["sha"] if obj["object"]["type"] == "commit" else ""
        return Tag(sha, obj["message"].rstrip("\n"))

    def release(self, version):
        release = self.request(f"/releases/tags/{version}", missing=True)[0]
        # GitHub's tag lookup can omit drafts.
        if release is None:
            matches = [item for item in self.pages("/releases") if item["tag_name"] == version]
            if len(matches) > 1:
                raise Error(f"Multiple releases for {version}")
            release = next(iter(matches), None)
        if release is None:
            return None
        if release["draft"] or release["prerelease"]:
            raise Error(f"Release {version} is a draft or prerelease")
        return release.get("body") or ""

    def create_tag(self, entry):
        tag, _ = self.request("/git/tags", {
            "tag": entry.version, "message": entry.marker, "object": entry.pull.sha, "type": "commit",
            "tagger": {
                "name": "github-actions[bot]",
                "email": "41898282+github-actions[bot]@users.noreply.github.com",
                "date": git("show", "-s", "--format=%cI", entry.pull.sha),
            },
        })
        self.request("/git/refs", {"ref": "refs/tags/" + entry.version, "sha": tag["sha"]})
        print(f"Created {entry.version} for PR #{entry.pull.number}")

    def create_release(self, entry):
        self.request("/releases", {
            "tag_name": entry.version, "target_commitish": entry.pull.sha,
            "name": entry.version, "body": entry.body,
        })
        print(f"Published {entry.version} for PR #{entry.pull.number}")


def git(*args):
    result = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    if result.returncode:
        raise Error(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def correction_settings(raw):
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise Error("RELEASE_CORRECTIONS must be a JSON object")
    for key, kind in data.items():
        if not re.fullmatch(r"[1-9][0-9]*:[0-9a-f]{40}", key) or kind not in LABELS.values():
            raise Error(f"Invalid release correction: {key}")
    return data


def pull_for(github, sha, ignored, corrections):
    associated = list(github.pages(f"/commits/{sha}/pulls"))
    candidates = []
    for item in associated:
        if item["base"]["ref"] != "main":
            continue
        pull = github.get(f"/pulls/{item['number']}")
        if pull["merged"] and pull["merge_commit_sha"] == sha and pull["base"]["ref"] == "main":
            candidates.append(pull)
    if not candidates and sha in ignored:
        return None
    if len(candidates) != 1 or sha in ignored:
        raise Error(f"{sha}: no unique eligible merge or conflicting direct-push exclusion. Verify the merge method or record a verified direct push.")
    pull = candidates[0]
    number = pull["number"]
    events = list(github.pages(f"/issues/{number}/timeline"))
    key = f"{number}:{sha}"
    try:
        # Corrections can resolve classification, never missing merge provenance.
        verify_merge(events, sha, pull["merged_at"])
        try:
            kind = merge_labels(events, pull["merged_at"])
        except Error:
            if key not in corrections:
                raise
            kind = corrections[key]
            print(f"Using configured release correction for PR #{number} at {sha}")
        else:
            if key in corrections:
                raise Error("a correction exists for a valid classification")
    except Error as error:
        raise Error(f"PR #{number} at {sha}: {error}. Verify merge-time labels and rerun; use a scoped correction only for classification.") from None
    match = NOTES.search(pull["body"] or "")
    notes = re.sub(r"<!--.*?-->", "", match[1], flags=re.S).strip() if match else ""
    return Pull(number, sha, f"https://github.com/{github.repo}/pull/{number}", kind, notes)


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
        event = json.load(stream)
    trigger = event["pull_request"]
    if event["action"] != "closed" or not trigger["merged"] or trigger["base"]["ref"] != "main":
        return

    github = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    baseline = os.getenv("RELEASE_BASE_TAG", "")
    boundary = os.getenv("RELEASE_AFTER_SHA", "")
    trigger_sha = trigger["merge_commit_sha"]
    if version_key(baseline) is None or not SHA.fullmatch(boundary):
        raise Error("Set RELEASE_BASE_TAG and RELEASE_AFTER_SHA before replay. See docs/releases.md.")
    ignored = set(os.getenv("RELEASE_DIRECT_PUSH_SHAS", "").split())
    if any(not SHA.fullmatch(sha) for sha in ignored):
        raise Error("RELEASE_DIRECT_PUSH_SHAS must contain full commit SHAs")
    corrections = correction_settings(os.getenv("RELEASE_CORRECTIONS"))
    git("fetch", "--tags", "origin", "refs/heads/main")
    history = git("rev-list", "--first-parent", "--reverse", "FETCH_HEAD").splitlines()
    if boundary not in history or trigger_sha not in history:
        raise Error("Activation boundary and trigger merge must both be on current first-parent main")
    next_index = history.index(boundary) + 1
    if history.index(trigger_sha) < next_index:
        return
    tags = {name: git("rev-parse", f"refs/tags/{name}^{{commit}}")
            for name in git("tag", "--list").splitlines() if version_key(name) is not None}
    if tags.get(baseline) not in history[:next_index]:
        raise Error("Stable baseline tag is missing or is not on first-parent history before activation")
    legacy_commits = set(history[:history.index(tags[baseline]) + 1])
    legacy = {name for name, sha in tags.items() if sha in legacy_commits}
    if highest_stable(legacy) != baseline:
        raise Error("Baseline must be the highest legacy stable tag")
    existing = set(tags) - legacy
    existing.update(
        item["tag_name"] for item in github.pages("/releases")
        if version_key(item["tag_name"]) is not None and item["tag_name"] not in legacy
    )

    pulls = []
    blocked = None
    for sha in history[next_index:]:
        try:
            pulls.append(pull_for(github, sha, ignored, corrections))
        except Error as error:
            blocked = error
            break
    publish(github, plan(baseline, pulls), existing)
    if blocked is not None:
        raise blocked


if __name__ == "__main__":
    try:
        main()
    except (Error, KeyError, ValueError) as error:
        print(f"Release workflow stopped: {error}", file=sys.stderr)
        sys.exit(1)
