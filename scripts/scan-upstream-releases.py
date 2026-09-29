#!/usr/bin/env python3
"""Open a PR for any plugin whose upstream repo has published a newer release.

A plugin's own repo normally pushes its release into this catalog. When that
push breaks the catalog goes stale and people install an old client, with
nothing to say so. This is the backstop, and it does not care why the push
did not happen.

  scripts/scan-upstream-releases.py --dry-run           # report, change nothing
  scripts/scan-upstream-releases.py --plugin assistant  # just the one
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import urllib.request

import yaml

PLUGINS_DIR = "plugins"
RELEASE_URI = re.compile(r"https://github\.com/([^/]+/[^/]+)/releases/download/")
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


def latest_release(repo):
    """Newest published release for a repo. Prereleases and drafts are not it."""
    result = run("gh", "api", f"repos/{repo}/releases/latest", "--jq", ".tag_name")
    return result.stdout.strip() if result.returncode == 0 else None


def published_checksums(repo, tag):
    """asset name -> sha256, from the release's checksums.txt if it has one."""
    out = run("gh", "release", "download", tag, "--repo", repo,
              "--pattern", "checksums.txt", "--dir", "/tmp/upstream-sums", "--clobber")
    if out.returncode != 0:
        return {}
    sums = {}
    with open("/tmp/upstream-sums/checksums.txt") as handle:
        for line in handle:
            digest, _, name = line.strip().partition("  ")
            if name:
                sums[name] = digest
    return sums


def sha256_of(url):
    """For releases that publish no checksums.txt."""
    with urllib.request.urlopen(url) as response:
        digest = hashlib.sha256()
        for chunk in iter(lambda: response.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_for(manifest):
    """The repo a plugin's archives come from, or None if not GitHub releases."""
    match = RELEASE_URI.match(manifest["spec"]["platforms"][0]["uri"])
    return match.group(1) if match else None


def retarget(text, old, new, sums, on_missing):
    """Point a manifest at `new`, giving each platform its own archive's digest.

    Pairs every uri with the sha256 line that follows it. Getting that pairing
    wrong would hand one platform another's digest, which installs cleanly for
    nobody.
    """
    text = text.replace(f"version: {old}", f"version: {new}")
    text = text.replace(f"/releases/download/{old}/", f"/releases/download/{new}/")

    lines, pending = [], None
    for line in text.split("\n"):
        uri = re.match(r"^\s*uri: (\S+)$", line)
        if uri:
            pending = uri.group(1)
        digest = re.match(r"^(\s*)sha256: \w+$", line)
        if digest and pending:
            asset = pending.rsplit("/", 1)[-1]
            value = sums.get(asset) or on_missing(pending)
            line = f"{digest.group(1)}sha256: {value}"
            pending = None
        lines.append(line)
    return "\n".join(lines)


def pr_body(name, repo, current, newest):
    return (
        f"`{name}` is on {current} here; {repo} has published **{newest}**.\n\n"
        "Opened by the daily upstream scan. A plugin's own repo normally pushes "
        "its release to this catalog; this runs regardless of whether that "
        "happened, so the catalog cannot drift unnoticed.\n\n"
        "Checksums come from the release's `checksums.txt` where it has one, and "
        "are computed from the archives where it does not. PR validation "
        "downloads every URI and verifies them again."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching git")
    parser.add_argument("--plugin", help="only this plugin, by name")
    args = parser.parse_args()

    opened, skipped, behind = [], [], []
    matched = False

    for filename in sorted(os.listdir(PLUGINS_DIR)):
        if not filename.endswith(".yaml"):
            continue
        path = os.path.join(PLUGINS_DIR, filename)
        with open(path) as handle:
            manifest = yaml.safe_load(handle)
        name = manifest["metadata"]["name"]
        if args.plugin and name != args.plugin:
            continue
        matched = True

        current = manifest["spec"]["version"]
        repo = repo_for(manifest)
        if not repo:
            skipped.append(f"{name}: archives are not on GitHub releases")
            continue

        newest = latest_release(repo)
        if not newest:
            skipped.append(f"{name}: {repo} has no published release")
            continue
        if newest == current:
            continue

        behind.append(f"{name}: {current} -> {newest} (from {repo})")
        if args.dry_run:
            continue

        branch = f"chore/{name}-{newest}"
        if run("git", "ls-remote", "--exit-code", "--heads", "origin", branch).returncode == 0:
            skipped.append(f"{name}: {branch} already exists")
            continue

        failures = []

        def on_missing(url, failures=failures):
            try:
                return sha256_of(url)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                failures.append(f"{url.rsplit('/', 1)[-1]}: {exc}")
                return "unknown"

        rewritten = retarget(open(path).read(), current, newest,
                             published_checksums(repo, newest), on_missing)
        if failures:
            skipped.append(f"{name}: could not hash {'; '.join(failures)}")
            continue

        with open(path, "w") as handle:
            handle.write(rewritten)

        run("git", "checkout", "-b", branch)
        run("git", "add", path)
        run("git", "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
            "commit", "-m", f"chore: Update the {name} plugin to {newest}")
        push = run("git", "push", "-u", "origin", branch)
        if push.returncode != 0:
            skipped.append(f"{name}: push failed: {push.stderr.strip()}")
            run("git", "checkout", "--", path)
            run("git", "checkout", "main")
            continue

        run("gh", "pr", "create", "--base", "main", "--head", branch,
            "--title", f"Update the {name} plugin to {newest}",
            "--body", pr_body(name, repo, current, newest))
        opened.append(f"{name}: {current} -> {newest}")
        run("git", "checkout", "main")

    # A typo in --plugin must not read as an all-clear.
    if args.plugin and not matched:
        print(f"no plugin named {args.plugin!r} in {PLUGINS_DIR}/", file=sys.stderr)
        return 1

    report(args, opened, skipped, behind)
    return 0


def report(args, opened, skipped, behind):
    lines = ["## Upstream release scan", ""]
    if args.dry_run:
        lines += [f"- behind: {item}" for item in behind] or ["- everything is up to date"]
        lines += ["", "_Dry run: nothing was changed._"]
    else:
        lines += [f"- opened {item}" for item in opened] or ["- nothing to update"]
    if skipped:
        lines += ["", "### Skipped", ""] + [f"- {item}" for item in skipped]

    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write(text + "\n")


if __name__ == "__main__":
    sys.exit(main())
