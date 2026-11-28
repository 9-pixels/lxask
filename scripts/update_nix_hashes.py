"""Refresh nix/hashes.json from a release's SHA256SUMS.

    python scripts/update_nix_hashes.py              # the release matching rust/Cargo.toml (vX.Y.Z)
    python scripts/update_nix_hashes.py v1.5.12      # a given release
    python scripts/update_nix_hashes.py --check      # exit 1 if nix/hashes.json is out of date
    python scripts/update_nix_hashes.py --sums OUT_DIR/SHA256SUMS v1.5.12   # a local file

nix/hashes.json names the model assets the Nix package fetches (nix/packages.nix) and their
hashes in the SRI form Nix wants. scripts/release_files.sh writes the same sums in hex into
the release's SHA256SUMS; this converts them.

Only the files already listed in nix/hashes.json are updated. A release that renames or drops
one of them (a new training round) also changes the names in nix/packages.nix, so the script
stops and says which, instead of writing a file the package cannot use.

Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASHES = ROOT / "nix" / "hashes.json"
SUMS_URL = "https://github.com/arxhr007/lxask/releases/download/{tag}/SHA256SUMS"
TAG = re.compile(r"v\d+\.\d+\.\d+")
# what scripts/release_files.sh puts into SHA256SUMS besides the model assets
NOT_A_MODEL_ASSET = re.compile(r"lxask-(x86_64|aarch64).*|.*\.whl|LICENSE|NOTICE|install\.sh")


def cargo_version(root: Path = ROOT) -> str:
    """The version in rust/Cargo.toml, found the way scripts/release_files.sh finds it."""
    for line in (root / "rust" / "Cargo.toml").read_text().splitlines():
        match = re.fullmatch(r'version = "([^"]+)"', line)
        if match:
            return match.group(1)
    raise SystemExit("rust/Cargo.toml has no `version = \"...\"` line")


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output (`<hex>  <name>`) as {name: hex}."""
    sums = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S.*)", line)
        if not match:
            raise SystemExit(f"unexpected line in SHA256SUMS: {line!r}")
        sums[match.group(2)] = match.group(1)
    return sums


def to_sri(hex_digest: str) -> str:
    """A sha256 in hex as the `sha256-<base64>` string Nix's fetchurl takes as `hash`."""
    return "sha256-" + base64.b64encode(bytes.fromhex(hex_digest)).decode()


def unlisted(current: dict, sums: dict[str, str]) -> list[str]:
    """Model assets the release lists that nix/hashes.json does not."""
    return sorted(
        name for name in sums if name not in current["model"] and not NOT_A_MODEL_ASSET.fullmatch(name)
    )


def refresh(current: dict, sums: dict[str, str], tag: str) -> dict:
    """`current` with the release set to `tag` and every listed file's hash from `sums`."""
    missing = [name for name in current["model"] if name not in sums]
    if missing:
        extra = unlisted(current, sums)
        raise SystemExit(
            f"SHA256SUMS of {tag} does not list {', '.join(missing)}"
            + (f" but lists {', '.join(extra)}" if extra else "")
            + ".\nThe release renamed a model file: update nix/hashes.json and the names in "
            "nix/packages.nix by hand."
        )
    return {"release": tag, "model": {name: to_sri(sums[name]) for name in current["model"]}}


def download(tag: str) -> str:
    """The release's SHA256SUMS."""
    url = SUMS_URL.format(tag=tag)
    request = urllib.request.Request(url, headers={"User-Agent": "lxask-nix-hashes"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        hint = f" (is {tag} published, with a SHA256SUMS?)" if error.code == 404 else ""
        raise SystemExit(f"{url}: HTTP {error.code}{hint}") from None
    except urllib.error.URLError as error:
        raise SystemExit(f"{url}: {error.reason}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh nix/hashes.json from a release's SHA256SUMS.")
    parser.add_argument("tag", nargs="?", help="release tag, vX.Y.Z (default: the version in rust/Cargo.toml)")
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if the file is out of date")
    parser.add_argument("--sums", type=Path, help="read this SHA256SUMS instead of downloading it")
    parser.add_argument("--file", type=Path, default=HASHES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    tag = args.tag or f"v{cargo_version()}"
    if not TAG.fullmatch(tag):
        raise SystemExit(f"{tag!r} is not a release tag like v1.5.11")

    sums = parse_sums(args.sums.read_text() if args.sums else download(tag))
    old_text = args.file.read_text()
    current = json.loads(old_text)
    new_text = json.dumps(refresh(current, sums, tag), indent=2) + "\n"
    for name in unlisted(current, sums):
        print(f"note: {tag} also lists {name}, which nix/hashes.json does not use", file=sys.stderr)

    if new_text == old_text:
        print(f"{args.file.name} is already up to date for {tag}")
        return 0
    if args.check:
        print(f"{args.file.name} is out of date for {tag}: run scripts/update_nix_hashes.py", file=sys.stderr)
        return 1
    args.file.write_text(new_text)
    print(f"{args.file.name} updated for {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
