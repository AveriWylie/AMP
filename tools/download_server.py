"""Download and verify an official Minecraft server JAR from Mojang."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def read_json(url):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = read_json(MANIFEST_URL)
    release = next((item for item in manifest["versions"] if item["id"] == args.version), None)
    if release is None:
        raise SystemExit(f"Minecraft version {args.version} was not found in Mojang's manifest")
    details = read_json(release["url"])
    download = details.get("downloads", {}).get("server")
    if download is None:
        raise SystemExit(f"Mojang does not publish a server JAR for {args.version}")
    with urlopen(download["url"], timeout=120) as response:
        payload = response.read()
    if hashlib.sha1(payload).hexdigest() != download["sha1"].lower():
        raise SystemExit("The downloaded server JAR failed Mojang's SHA-1 integrity check")
    args.destination.write_bytes(payload)


if __name__ == "__main__":
    main()
