import json

from tools.discover_minecraft_releases import (
    discover_candidates,
    update_candidates,
)


def test_discovery_selects_untracked_stable_java_26_and_later():
    manifest = {
        "versions": [
            {
                "id": "26.2",
                "type": "release",
                "releaseTime": "2026-06-16T12:03:33Z",
                "url": "https://example.test/26.2",
            },
            {
                "id": "26.3-snapshot-1",
                "type": "snapshot",
                "releaseTime": "2026-07-01T00:00:00Z",
                "url": "https://example.test/snapshot",
            },
            {
                "id": "1.21.9",
                "type": "release",
                "releaseTime": "2025-01-01T00:00:00Z",
                "url": "https://example.test/old",
            },
            {
                "id": "27.1",
                "type": "release",
                "releaseTime": "2027-03-01T00:00:00Z",
                "url": "https://example.test/27.1",
            },
            {
                "id": "26.3",
                "type": "release",
                "releaseTime": "2026-09-01T00:00:00Z",
                "url": "https://example.test/26.3",
            },
        ]
    }

    assert [
        candidate["version"]
        for candidate in discover_candidates(manifest, {"26.2"})
    ] == ["26.3", "27.1"]


def test_update_writes_candidates_and_removes_empty_record(tmp_path, monkeypatch):
    support = tmp_path / "support.json"
    output = tmp_path / "candidates.json"
    support.write_text(json.dumps({
        "sources": {"version_manifest": "https://example.test/manifest"},
        "versions": {"26.2": {}},
    }), encoding="utf-8")
    manifest = {"versions": [{
        "id": "26.3",
        "type": "release",
        "releaseTime": "2026-09-01T00:00:00Z",
        "url": "https://example.test/26.3",
    }]}
    monkeypatch.setattr(
        "tools.discover_minecraft_releases.fetch_manifest",
        lambda _url: manifest,
    )

    assert update_candidates(support, output)[0]["version"] == "26.3"
    assert json.loads(output.read_text(encoding="utf-8"))["candidates"][0][
        "version"
    ] == "26.3"

    support.write_text(json.dumps({
        "sources": {"version_manifest": "https://example.test/manifest"},
        "versions": {"26.2": {}, "26.3": {}},
    }), encoding="utf-8")
    assert update_candidates(support, output) == []
    assert not output.exists()
