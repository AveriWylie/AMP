from tools.check_version_data import completeness_errors


def test_checked_in_data_covers_manifest():
    assert completeness_errors() == []


def test_completeness_reports_protocol_and_registry_gaps(tmp_path):
    manifest = {
        "versions": {"26.9": {"protocol": 999, "family": "java-26.9", "status": "pending"}}
    }
    table = {
        "versions": {
            "26.9": {
                "protocol": 998,
                "clientbound": {},
                "serverbound": {},
                "states": {},
            }
        }
    }

    errors = completeness_errors(tmp_path, manifest, table)

    assert "26.9: protocol number differs from manifest" in errors
    assert "26.9: missing configuration/serverbound table" in errors
    assert "26.9: missing blocks registry" in errors
