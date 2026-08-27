"""Record untracked stable Minecraft releases for a candidate update PR."""

import argparse
import json
import re
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPORT_PATH = ROOT / "protocol" / "version_support.json"
CANDIDATE_PATH = ROOT / "protocol" / "minecraft_release_candidates.json"
NUMERIC_RELEASE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


def fetch_manifest(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AMP-version-candidate-check"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def discover_candidates(manifest, tracked_versions):
    candidates = []
    for release in manifest.get("versions", []):
        match = NUMERIC_RELEASE.fullmatch(release.get("id", ""))
        if (
            release.get("type") != "release"
            or match is None
            or int(match.group(1)) < 26
            or release["id"] in tracked_versions
        ):
            continue
        candidates.append({
            "version": release["id"],
            "release_time": release["releaseTime"],
            "metadata_url": release["url"],
        })
    return sorted(candidates, key=lambda item: item["release_time"])


def update_candidates(support_path=SUPPORT_PATH, output_path=CANDIDATE_PATH):
    support = json.loads(Path(support_path).read_text(encoding="utf-8"))
    source = support["sources"]["version_manifest"]
    manifest = fetch_manifest(source)
    candidates = discover_candidates(manifest, set(support["versions"]))
    output_path = Path(output_path)
    if not candidates:
        if output_path.exists():
            output_path.unlink()
        return []
    rendered = json.dumps({
        "source": source,
        "candidates": candidates,
    }, indent=2, sort_keys=True) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8", newline="\n")
    return candidates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support", type=Path, default=SUPPORT_PATH)
    parser.add_argument("--output", type=Path, default=CANDIDATE_PATH)
    args = parser.parse_args(argv)
    candidates = update_candidates(args.support, args.output)
    if candidates:
        print("Untracked stable releases: " + ", ".join(
            candidate["version"] for candidate in candidates
        ))
    else:
        print("No untracked stable Minecraft releases")


if __name__ == "__main__":
    main()
