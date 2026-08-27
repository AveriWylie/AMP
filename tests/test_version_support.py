import json

import pytest

from version_support import (
    load_support_manifest,
    pending_versions,
    runnable_version_protocols,
)


def test_checked_in_manifest_tracks_only_stable_26x_targets():
    manifest = load_support_manifest()

    assert manifest["primary"] is None
    assert {
        version: (entry["protocol"], entry["family"], entry["status"])
        for version, entry in manifest["versions"].items()
    } == {
        "26.1": (775, "java-26.1", "supported"),
        "26.1.1": (775, "java-26.1", "supported"),
        "26.1.2": (775, "java-26.1", "supported"),
        "26.2": (776, "java-26.2", "supported"),
    }


def test_manifest_rejects_snapshot_targets(tmp_path):
    path = tmp_path / "support.json"
    path.write_text(json.dumps({
        "primary": None,
        "versions": {
            "26.3-snapshot-10": {
                "release_type": "snapshot",
                "protocol": 1073742156,
                "family": "java-26.3",
                "status": "pending",
            }
        },
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="stable releases"):
        load_support_manifest(path)


def test_manifest_rejects_unverified_primary(tmp_path):
    path = tmp_path / "support.json"
    path.write_text(json.dumps({
        "primary": "26.2",
        "versions": {
            "26.2": {
                "release_type": "release",
                "protocol": 776,
                "family": "java-26.2",
                "status": "pending",
            }
        },
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="primary version must be supported"):
        load_support_manifest(path)


def test_runtime_versions_exclude_pending_targets():
    assert runnable_version_protocols() == {
        "26.1": 775,
        "26.1.1": 775,
        "26.1.2": 775,
        "26.2": 776,
    }
    assert pending_versions() == ()
