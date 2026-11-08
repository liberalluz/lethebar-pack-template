"""Build a pack directory into a reproducible zip, hash it, and publish it.

A pack reaches a player either compiled into the build or imported by hand. Once a
world repository owns its packs, both stop working: the pack has to travel as an
artifact with a hash an engine can check. This tool is that step.

Reproducibility is the point. Zip records a timestamp per entry and walks the
directory in whatever order the filesystem hands back, so a build fixes both: one
timestamp for every entry and a sorted name list. Two builds of one directory are
then byte-identical, which is what makes a hash worth writing down.

    python infra/tool/release/pack.py build content/builtin/<id> --out dist
    python infra/tool/release/pack.py hash dist/<id>.zip
    python infra/tool/release/pack.py publish dist/<id>.zip --repo o/r --tag pack/<id>/vX.Y.Z

`hash` writes `<id>-<version>.json` next to the zip: the id, the version, the byte
count, the sha256, the specVersion and the engine range, which is what an engine
needs to decide whether it can install the artifact at all.

`publish` is dry by default and only touches the network with `--apply`. An upload
attaches files to a release that already exists, so `publish` creates the release
first when the tag has none: the first publication of any pack would otherwise fail
with `release not found`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
MANIFEST = "manifest.json"


def gh() -> str:
    """The GitHub client, overridable so a test can prove the order of calls."""
    return os.environ.get("LETHEBAR_GH", "gh")


def files_of(root: Path) -> list[Path]:
    """Every file of the pack, in a stable order, manifest first."""
    entries: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries.append(path.relative_to(root))
    entries.sort(key=lambda item: (item.as_posix() != MANIFEST, item.as_posix()))
    return entries


def build(root: Path, out_dir: Path | None = None) -> Path:
    """Write a zip of `root` and return its path."""
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise SystemExit(f"{root} carries no {MANIFEST}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack_id = str(manifest.get("id", "")).strip()
    if pack_id == "":
        raise SystemExit(f"{manifest_path} names no id")

    out_dir = out_dir or Path("dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{pack_id}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files_of(root):
            info = zipfile.ZipInfo(relative.as_posix(), date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (root / relative).read_bytes())
    return target


def digest(target: Path) -> str:
    return hashlib.sha256(target.read_bytes()).hexdigest()


def describe(target: Path) -> dict:
    """The manifest an engine reads before it installs the artifact."""
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    data = target.read_bytes()
    return {
        "id": str(manifest.get("id", "")),
        "version": str(manifest.get("version", "")),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "specVersion": str(manifest.get("specVersion", "")),
        "engine": str(manifest.get("engine", "")),
    }


def write_descriptor(target: Path) -> Path:
    described = describe(target)
    sidecar = target.with_name(f"{described['id']}-{described['version']}.json")
    sidecar.write_text(
        json.dumps(described, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return sidecar


def release_exists(repo: str, tag: str) -> bool:
    """Ask GitHub whether the tag already carries a release."""
    probe = [gh(), "release", "view", tag, "--repo", repo]
    return subprocess.run(
        probe, check=False, capture_output=True, text=True
    ).returncode == 0


def create_command(repo: str, tag: str) -> list[str]:
    return [
        gh(),
        "release",
        "create",
        tag,
        "--repo",
        repo,
        "--title",
        tag,
        "--generate-notes",
    ]


def upload_command(target: Path, sidecar: Path, repo: str, tag: str) -> list[str]:
    return [
        gh(),
        "release",
        "upload",
        tag,
        str(target),
        str(sidecar),
        "--repo",
        repo,
        "--clobber",
    ]


def validate(target: Path) -> dict:
    """Refuse a pack that cannot be named, before any command is built."""
    described = describe(target)
    missing = [field for field in ("id", "version") if not described.get(field)]
    if missing:
        raise SystemExit(f"{target} names no {', '.join(missing)}; preflight says the same")
    if described["sha256"] != digest(target):
        raise SystemExit(f"{target} hashed differently twice; refusing to publish")
    return described


def publish_commands(target: Path, repo: str, tag: str, exists: bool) -> list[list[str]]:
    """The commands a publication needs, in the order it needs them.

    The descriptor is written here, so the bytes a player will verify are the bytes
    this run produced. A pack whose manifest carries no id or version cannot be
    named in a descriptor, and is refused rather than published under an empty name.
    """
    validate(target)
    sidecar = write_descriptor(target)
    commands: list[list[str]] = []
    if not exists:
        commands.append(create_command(repo, tag))
    commands.append(upload_command(target, sidecar, repo, tag))
    return commands


def write_fixture(directory: Path) -> Path:
    """Write a pack for the self-test, so the tool needs no shipped story."""
    root = directory / "fixture-pack"
    (root / "data").mkdir(parents=True)
    manifest = {
        "id": "fixture-pack",
        "version": "0.1.0",
        "specVersion": "2.0.0",
        "engine": ">=4.7.0 <5.0.0",
    }
    (root / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "data" / "nodes.json").write_text(
        '{"n1": {"text": "a fixture pack"}}\n',
        encoding="utf-8",
        newline="\n",
    )
    return root


def self_test() -> int:
    """Assert the properties this tool exists for, without touching the network."""
    problems: list[str] = []
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as source,
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as first,
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as second,
    ):
        root = write_fixture(Path(source))
        one = build(root, Path(first))
        two = build(root, Path(second))
        if one.read_bytes() != two.read_bytes():
            problems.append("two builds of one directory differ")
        if digest(one) != digest(two):
            problems.append("two builds hash differently")
        described = describe(one)
        for field in ("id", "version", "bytes", "sha256", "specVersion", "engine"):
            if not described.get(field):
                problems.append(f"the descriptor has no {field}")
        if described["sha256"] != digest(one):
            problems.append("the descriptor hash is not the file hash")
        if described["bytes"] != one.stat().st_size:
            problems.append("the descriptor size is not the file size")
        sidecar = write_descriptor(one)
        written = json.loads(sidecar.read_text(encoding="utf-8"))
        if written != described:
            problems.append("the sidecar disagrees with the descriptor")
        command = publish_commands(one, "liberalluz/example", "pack/fixture-pack/v0.1.0", False)
        if len(command) != 2:
            problems.append("a missing release does not produce a create and an upload")
        if command[0][:3] != [gh(), "release", "create"]:
            problems.append("the create command is not the first one")
        if command[1][:3] != [gh(), "release", "upload"]:
            problems.append("the upload command is not second")
        if "--clobber" not in command[1]:
            problems.append("the upload command does not replace an artifact")
        if publish_commands(one, "liberalluz/example", "pack/fixture-pack/v0.1.0", True)[0][:3] != [
            gh(),
            "release",
            "upload",
        ]:
            problems.append("an existing release is created again")
    if problems:
        for problem in problems:
            print(f"[fail] {problem}")
        return 1
    print("[ok] two builds are identical; the descriptor names the bytes; publish uploads both files")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build, hash and publish a pack.")
    commands = parser.add_subparsers(dest="command", required=True)

    build_parser = commands.add_parser("build", help="build a pack directory into a zip")
    build_parser.add_argument("root")
    build_parser.add_argument("--out", default="dist")

    hash_parser = commands.add_parser("hash", help="name the bytes of a built pack")
    hash_parser.add_argument("zip")

    publish_parser = commands.add_parser("publish", help="upload a pack to a release")
    publish_parser.add_argument("zip")
    publish_parser.add_argument("--repo", required=True)
    publish_parser.add_argument("--tag", required=True)
    publish_parser.add_argument("--apply", action="store_true")

    commands.add_parser("self-test", help="assert reproducibility and the descriptor")

    args = parser.parse_args(argv)

    if args.command == "build":
        target = build(Path(args.root), Path(args.out))
        print(f"built {target} sha256:{digest(target)}")
        return 0

    if args.command == "hash":
        target = Path(args.zip)
        if not target.is_file():
            raise SystemExit(f"{target} does not exist")
        sidecar = write_descriptor(target)
        described = describe(target)
        print(f"sha256:{described['sha256']} {described['bytes']} bytes")
        print(f"wrote {sidecar}")
        return 0

    if args.command == "publish":
        target = Path(args.zip)
        if not target.is_file():
            raise SystemExit(f"{target} does not exist")
        if not args.apply:
            for command in publish_commands(target, args.repo, args.tag, False):
                print(" ".join(command))
            print("dry run; pass --apply to create the release and upload")
            return 0
        validate(target)
        exists = release_exists(args.repo, args.tag)
        print(f"release {args.tag} {'exists' if exists else 'is missing'}")
        for command in publish_commands(target, args.repo, args.tag, exists):
            print(" ".join(command))
            code = subprocess.run(command, check=False).returncode
            if code != 0:
                print(f"[fail] {command[2]} exited {code}", file=sys.stderr)
                return code
        return 0

    return self_test()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
