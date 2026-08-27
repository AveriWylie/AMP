"""Load and validate AMP's evidence-backed Minecraft version support policy."""

import json
from pathlib import Path


SUPPORT_MANIFEST_PATH = Path(__file__).parent / "protocol" / "version_support.json"
VALID_STATUSES = {"pending", "supported"}


def load_support_manifest(path=SUPPORT_MANIFEST_PATH):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise ValueError("support manifest must contain versions")

    for version, entry in versions.items():
        if entry.get("release_type") != "release":
            raise ValueError("support manifest may contain only stable releases")
        if not isinstance(entry.get("protocol"), int):
            raise ValueError(f"version {version} has no numeric protocol")
        if not isinstance(entry.get("family"), str) or not entry["family"]:
            raise ValueError(f"version {version} has no protocol family")
        if entry.get("status") not in VALID_STATUSES:
            raise ValueError(f"version {version} has invalid support status")
        if entry.get("status") == "supported" and not (
            entry.get("offline_verified") is True
            and entry.get("live_verified") is True
        ):
            raise ValueError(f"supported version {version} lacks verification evidence")

    target_primary = manifest.get("target_primary")
    if target_primary not in versions:
        raise ValueError("target primary must be present in versions")

    primary = manifest.get("primary")
    if primary is not None and (
        primary not in versions or versions[primary]["status"] != "supported"
    ):
        raise ValueError("primary version must be supported")
    return manifest


def runnable_version_protocols():
    """Return versions backed by verified adapters, plus the migration reference."""
    manifest = load_support_manifest()
    runnable = {
        version: entry["protocol"]
        for version, entry in manifest["versions"].items()
        if entry["status"] == "supported"
    }
    runnable[manifest["legacy_reference"]] = manifest["legacy_protocol"]
    return runnable


def pending_versions():
    manifest = load_support_manifest()
    return tuple(
        version for version, entry in manifest["versions"].items()
        if entry["status"] == "pending"
    )
