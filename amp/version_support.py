
# imports
import json
from pathlib import Path


# global constants
SUPPORT_MANIFEST_PATH = Path(__file__).parent / "protocol" / "version_support.json"
VALID_STATUSES = {"pending", "supported"}


"""
--------------------------------------------------------------------------------------------
Function Header - Manifest loader and policy gate
--------------------------------------------------------------------------------------------
Reads the support manifest and refuses to return anything that does not satisfy AMP's support
policy. Every check raises rather than warning or dropping the bad entry, because the manifest
is what the rest of AMP trusts when it decides which versions it can speak. A half-valid
manifest that loads is worse than one that fails, it means AMP advertises a version it cannot
actually play and you find out mid-session.

The interesting rule is the last one. A version may only claim "supported" if it carries both
offline_verified and live_verified. Support here means evidence exists, not that someone
thinks it probably works, so the flags are the gate and the status alone cannot grant it.

Snapshots are rejected outright. AMP tracks stable releases, and a snapshot's protocol can
change under you between builds while keeping the same version string, so there is nothing
stable to pin data against.

The primary check runs after the loop because it is a cross-entry rule, it needs every version
parsed before it can ask whether the one nominated as primary is actually supported.
--------------------------------------------------------------------------------------------
"""
def load_support_manifest(path=SUPPORT_MANIFEST_PATH):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    versions = manifest.get("versions")

    if not isinstance(versions, dict) or not versions:
        raise ValueError("support manifest must contain versions")

    # each entry has to stand on its own before any cross-entry rule is worth checking
    for version, entry in versions.items():
        if entry.get("release_type") != "release":
            raise ValueError("support manifest may contain only stable releases")

        if not isinstance(entry.get("protocol"), int):
            raise ValueError(f"version {version} has no numeric protocol")

        if not isinstance(entry.get("family"), str) or not entry["family"]:
            raise ValueError(f"version {version} has no protocol family")

        if entry.get("status") not in VALID_STATUSES:
            raise ValueError(f"version {version} has invalid support status")

        # claiming support requires the evidence, not just the label
        if entry.get("status") == "supported" and not (entry.get("offline_verified") is True and entry.get("live_verified") is True):
            raise ValueError(f"supported version {version} lacks verification evidence")

    # cross-entry rule, so it waits until every version above has parsed
    primary = manifest.get("primary")

    if primary is not None and (primary not in versions or versions[primary]["status"] != "supported"):
        raise ValueError("primary version must be supported")

    return manifest


"""
--------------------------------------------------------------------------------------------
Function Field Header - Support queries
--------------------------------------------------------------------------------------------
Two views over the same manifest, split by status. Runnable versions are the ones Bot will
actually connect with, and it returns the protocol number rather than the version string
because the handshake sends a number and every packet ID is keyed to it.

Pending versions are tracked but not offered. They exist so AMP can say "this release is
known, its data is not verified yet" instead of failing as though the version never existed,
which is a much more useful thing to tell someone typing a version at the CLI.
--------------------------------------------------------------------------------------------
"""
def runnable_version_protocols():
    manifest = load_support_manifest()

    return {version: entry["protocol"] for version, entry in manifest["versions"].items() if entry["status"] == "supported"}


def pending_versions():
    manifest = load_support_manifest()

    return tuple(version for version, entry in manifest["versions"].items() if entry["status"] == "pending")
