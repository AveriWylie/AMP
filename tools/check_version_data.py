"""Fail when the support manifest lacks generated protocol or registry artifacts."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_ROOT = ROOT / "amp"

from amp.protocol_data import load_protocol_tables  # noqa: E402
from amp.version_support import load_support_manifest  # noqa: E402


REQUIRED_STATES = ("login", "configuration")
REQUIRED_DIRECTIONS = ("clientbound", "serverbound")
REGISTRY_DIRECTORIES = ("blocks", "items", "entities")


def completeness_errors(root=DATA_ROOT, manifest=None, protocol_table=None):
    manifest = manifest or load_support_manifest()
    protocol_table = protocol_table or load_protocol_tables()
    errors = []
    for version, support in manifest["versions"].items():
        protocol = protocol_table["versions"].get(version)
        if protocol is None:
            errors.append(f"{version}: missing protocol table")
            continue
        if protocol.get("protocol") != support["protocol"]:
            errors.append(f"{version}: protocol number differs from manifest")
        for direction in REQUIRED_DIRECTIONS:
            if not protocol.get(direction):
                errors.append(f"{version}: missing play/{direction} table")
        for state in REQUIRED_STATES:
            for direction in REQUIRED_DIRECTIONS:
                if not protocol.get("states", {}).get(state, {}).get(direction):
                    errors.append(f"{version}: missing {state}/{direction} table")
        for directory in REGISTRY_DIRECTORIES:
            path = Path(root) / directory / f"{directory}_{version}.json"
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"{version}: missing {directory} registry")
    return errors


def main():
    errors = completeness_errors()
    if errors:
        print("Generated Minecraft data is incomplete:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Generated Minecraft data covers every manifest version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
