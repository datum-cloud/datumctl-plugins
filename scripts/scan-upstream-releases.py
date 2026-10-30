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
import json
import os
import re
import subprocess
import sys
import urllib.request

import yaml

PLUGINS_DIR = "plugins"
RELEASE_URI = re.compile(r"https://github\.com/([^/]+/[^/]+)/releases/download/")


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
        "Opened or updated by the daily upstream scan. A plugin's own repo normally pushes "
        "its release to this catalog; this runs regardless of whether that "
        "happened, so the catalog cannot drift unnoticed.\n\n"
        "Checksums come from the release's `checksums.txt` where it has one, and "
        "are computed from the archives where it does not. PR validation "
        "downloads every URI and verifies them again."
    )


def catalog_repo():
    """owner/name of this catalog: Actions sets GITHUB_REPOSITORY; by hand, gh knows."""
    return os.environ.get("GITHUB_REPOSITORY") or run(
        "gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").stdout.strip()


def open_pulls(catalog):
    """Open PRs into main from the catalog's own branches: number, branch, head commit."""
    jq = (f'.[] | select((.head.repo.full_name // "" | ascii_downcase) == "{catalog.lower()}")'
          " | {number, branch: .head.ref, sha: .head.sha}")
    out = run("gh", "api", "--paginate", f"repos/{catalog}/pulls?state=open&base=main&per_page=100", "--jq", jq)
    if out.returncode != 0:
        # Guessing "no open PRs" here would open duplicates, the thing this avoids.
        sys.exit(f"could not list the open PRs: {out.stderr.strip()}")
    return [json.loads(line) for line in out.stdout.splitlines()]


def pr_files(catalog, number):
    out = run("gh", "api", "--paginate", f"repos/{catalog}/pulls/{number}/files?per_page=100", "--jq", ".[].filename")
    return out.stdout.split()


def candidates(pulls, stable):
    """PRs on the stable branch first, then on its versioned branches, oldest first."""
    mine = [pr for pr in pulls if pr["branch"] == stable or pr["branch"].startswith(stable + "-")]
    return sorted(mine, key=lambda pr: (pr["branch"] != stable, pr["number"]))


def plugin_pr(catalog, pulls, name, path):
    """The open PR that carries this plugin's updates, chosen as the release action chooses it.

    datum-cloud/actions update-plugin-index (v1.22.0, resolve-pr-branch.sh) reuses the PR on
    update-plugin/<name>, else the oldest on update-plugin/<name>-*, when the plugin's manifest
    is its only file. Choosing the same PR keeps one open update per plugin, whoever opened it.
    """
    for pr in candidates(pulls, f"update-plugin/{name}"):
        if pr_files(catalog, pr["number"]) == [path]:
            return pr
    return None


def proposed_version(catalog, pr, path):
    """The plugin's version at the PR's head."""
    out = run("gh", "api", "-H", "Accept: application/vnd.github.raw",
              f"repos/{catalog}/contents/{path}?ref={pr['sha']}")
    return yaml.safe_load(out.stdout)["spec"]["version"] if out.returncode == 0 else None


def stable_taken(pulls, name):
    return any(pr["branch"] == f"update-plugin/{name}" for pr in pulls)


def choose_pr(catalog, pulls, name, path, newest):
    """The PR to update (None: open one), and why to leave the plugin alone, if so."""
    pr = plugin_pr(catalog, pulls, name, path)
    if pr and proposed_version(catalog, pr, path) == newest:
        return pr, f"#{pr['number']} already proposes {newest}"
    if not pr and stable_taken(pulls, name):
        # The release action refuses this case too, rather than overwrite another PR.
        return None, f"update-plugin/{name} carries a PR that changes more than {path}"
    return pr, None


def plan(pr, why, name):
    """What the scan does about a plugin that is behind, in a few words."""
    if why:
        return why
    return f"update #{pr['number']}" if pr else f"open a PR on update-plugin/{name}"


def publish(path, name, newest, pr, body):
    """Commit the manifest to the plugin's PR branch, then open or retitle the PR.

    The commit goes through the API so GitHub signs it. A reused branch is reset to main first,
    as the release action rewrites it. Returns why it failed, or None.
    """
    branch = pr["branch"] if pr else f"update-plugin/{name}"
    title = f"Update the {name} plugin to {newest}"
    base = run("git", "rev-parse", "main").stdout.strip()
    env = {k: v for k, v in os.environ.items() if k != "EXPECTED_HEAD"}
    env.update(BASE_SHA=base, GITHUB_REPOSITORY=catalog_repo())
    commit = subprocess.run(["scripts/sign-commit", branch, f"chore: {title}", path],
                            capture_output=True, text=True, env=env)
    run("git", "checkout", "--", path)
    if commit.returncode != 0:
        return f"signed commit failed: {commit.stderr.strip()}"
    if pr:
        run("gh", "pr", "edit", str(pr["number"]), "--title", title, "--body", body)
    else:
        run("gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching git")
    parser.add_argument("--plugin", help="only this plugin, by name")
    args = parser.parse_args()

    opened, skipped, behind = [], [], []
    matched = False
    catalog = catalog_repo()
    pulls = open_pulls(catalog)

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

        pr, why = choose_pr(catalog, pulls, name, path, newest)
        behind.append(f"{name}: {current} -> {newest} (from {repo}): {plan(pr, why, name)}")
        if args.dry_run:
            continue
        if why:
            skipped.append(f"{name}: {why}")
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

        failure = publish(path, name, newest, pr, pr_body(name, repo, current, newest))
        if failure:
            skipped.append(f"{name}: {failure}")
            continue
        opened.append(f"{name}: {current} -> {newest} ({plan(pr, None, name)})")

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
        lines += [f"- proposed {item}" for item in opened] or ["- nothing to update"]
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
