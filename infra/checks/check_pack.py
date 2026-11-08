#!/usr/bin/env python3
"""Check the archive a package publishes to.

Only the archive is read. A repository is a project: its tree, its scripts and
its build output are the author's business. The pack is the thing that travels
and the thing an engine installs, so the pack is what gets checked - once here,
before publishing, and once inside the engine after the download.

Every rule has a negative case in --self-test, because a rule nobody has seen
fire is a rule that is not really there.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ALLOWED_ROOTS = {"data", "assets", "i18n"}
MANIFEST = "manifest.json"
MAX_ENTRIES = 512
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
EXECUTABLE_SUFFIXES = {
    ".gd", ".exe", ".sh", ".dll", ".bat", ".cmd", ".ps1", ".py", ".js",
    ".mjs", ".so", ".dylib", ".app", ".apk", ".jar", ".wasm",
}
REQUIRED_FIELDS = ("id", "version", "specVersion", "engine", "kind", "protocol")
ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ENGINE_PATTERN = re.compile(r"^>=(\d+\.\d+\.\d+) <(\d+\.\d+\.\d+)$")


def normalized(name: str) -> tuple[str, list[str]]:
    """Return the entry name as posix text and the problems it carries."""

    problems: list[str] = []
    if not name:
        return name, ["an entry has an empty name"]
    if "\\" in name:
        problems.append(f"{name}: uses a backslash instead of a slash")
    text = name.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        problems.append(f"{text}: is an absolute path")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        problems.append(f"{text}: climbs out of the archive with ..")
    return text, problems


def check_entry(info: zipfile.ZipInfo, name: str, total: list[int]) -> list[str]:
    problems: list[str] = []
    text, name_problems = normalized(name)
    problems.extend(name_problems)
    if name_problems:
        return problems

    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        problems.append(f"{text}: is a symbolic link")
        return problems

    parts = PurePosixPath(text).parts
    if len(parts) == 1:
        if parts[0] != MANIFEST:
            problems.append(
                f"{text}: sits at the root; only {MANIFEST} may be there"
            )
        return problems

    if parts[0] not in ALLOWED_ROOTS:
        problems.append(
            f"{text}: lives under {parts[0]}/, which is not one of "
            + ", ".join(sorted(ALLOWED_ROOTS))
        )
    suffix = PurePosixPath(text).suffix.lower()
    if suffix in EXECUTABLE_SUFFIXES:
        problems.append(f"{text}: carries {suffix}, which a pack may not ship")
    if info.file_size > MAX_FILE_BYTES:
        problems.append(
            f"{text}: is {info.file_size} bytes, over the "
            f"{MAX_FILE_BYTES} byte file cap"
        )
    total[0] += info.file_size
    return problems


def check_manifest(payload: bytes) -> list[str]:
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"{MANIFEST}: does not parse ({error})"]
    if not isinstance(manifest, dict):
        return [f"{MANIFEST}: is not an object"]

    problems = [
        f"{MANIFEST}: {field} is missing" for field in REQUIRED_FIELDS
        if field not in manifest
    ]
    identifier = manifest.get("id")
    if isinstance(identifier, str) and not ID_PATTERN.match(identifier):
        problems.append(f"{MANIFEST}: id {identifier!r} is not [a-z0-9-]+")
    version = manifest.get("version")
    if isinstance(version, str) and not SEMVER_PATTERN.match(version):
        problems.append(f"{MANIFEST}: version {version!r} is not x.y.z")
    engine = manifest.get("engine")
    if isinstance(engine, str) and not ENGINE_PATTERN.match(engine):
        problems.append(
            f"{MANIFEST}: engine {engine!r} is not '>=x.y.z <x.y.z'"
        )
    return problems


def check_archive(path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path}: does not exist"]
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        return [f"{path}: is not a zip archive ({error})"]

    problems: list[str] = []
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            problems.append(
                f"the archive holds {len(infos)} entries, over the "
                f"{MAX_ENTRIES} entry cap"
            )
        total = [0]
        for info in infos:
            problems.extend(check_entry(info, info.filename, total))
        if total[0] > MAX_TOTAL_BYTES:
            problems.append(
                f"the archive holds {total[0]} bytes, over the "
                f"{MAX_TOTAL_BYTES} byte cap"
            )
        if MANIFEST not in archive.namelist():
            problems.append(f"{MANIFEST}: is not at the root")
            return problems
        problems.extend(check_manifest(archive.read(MANIFEST)))
    return problems


def fixture(directory: Path, name: str, files: dict[str, bytes],
            symlink: str = "", executable: str = "") -> Path:
    """Build a small archive that breaks exactly one rule."""

    target = directory / f"{name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry, payload in files.items():
            archive.writestr(entry, payload)
        special = executable or symlink
        if special:
            info = zipfile.ZipInfo(special)
            info.external_attr = (0o120777 << 16) if symlink else (0o100755 << 16)
            archive.writestr(info, b"#!/bin/sh\nexit 0\n")
    return target


def self_test() -> int:
    good_manifest = json.dumps({
        "id": "demo-pack",
        "version": "1.0.0",
        "specVersion": "2.0.0",
        "engine": ">=4.7.0 <5.0.0",
        "kind": "bundle",
        "protocol": "linear@1",
    }).encode("utf-8")
    good = {
        MANIFEST: good_manifest,
        "data/nodes.json": b'{"nodes": []}',
        "assets/scene.png": b"\x89PNG\r\n\x1a\n",
        "i18n/en.json": b'{"n1": {"text": "hello"}}',
    }

    # The fixtures live beside the checker rather than in the system temporary
    # directory: a sandboxed or locked-down runner can refuse that one, and a
    # self-test that cannot write its own fixtures proves nothing.
    scratch = Path(__file__).resolve().parents[2] / ".test-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.mkdtemp(prefix="lethebar-pack-", dir=scratch)
    try:
        directory = Path(temporary)
        cases: list[tuple[str, Path, str, bool]] = [
            ("a clean pack", fixture(directory, "clean", good), "", True),
            (
                "a stray root file",
                fixture(directory, "stray", {**good, "README.md": b"hi"}),
                "sits at the root",
                False,
            ),
            (
                "an entry that climbs out",
                fixture(directory, "climb", {**good, "../escape.json": b"{}"}),
                "climbs out",
                False,
            ),
            (
                "an absolute entry",
                fixture(directory, "absolute", {**good, "/etc/passwd": b"x"}),
                "absolute path",
                False,
            ),
            (
                "a symbolic link",
                fixture(directory, "link", good, symlink="data/link.json"),
                "symbolic link",
                False,
            ),
            (
                "an executable entry",
                fixture(directory, "script", good, executable="data/run.sh"),
                "carries .sh",
                False,
            ),
            (
                "a root the envelope does not allow",
                fixture(directory, "unknown-root", {**good, "extra/a.json": b"{}"}),
                "lives under extra/",
                False,
            ),
            (
                "a manifest that is not at the root",
                fixture(directory, "no-manifest", {"data/nodes.json": b"{}"}),
                "is not at the root",
                False,
            ),
            (
                "an id that is not a slug",
                fixture(directory, "bad-id", {
                    **good,
                    MANIFEST: json.dumps({
                        "id": "Demo Pack",
                        "version": "1.0.0",
                        "specVersion": "2.0.0",
                        "engine": ">=4.7.0 <5.0.0",
                        "kind": "bundle",
                        "protocol": "linear@1",
                    }).encode("utf-8"),
                }),
                "is not [a-z0-9-]+",
                False,
            ),
            (
                "an engine range that is not a range",
                fixture(directory, "bad-engine", {
                    **good,
                    MANIFEST: json.dumps({
                        "id": "demo-pack",
                        "version": "1.0.0",
                        "specVersion": "2.0.0",
                        "engine": "4.7",
                        "kind": "bundle",
                        "protocol": "linear@1",
                    }).encode("utf-8"),
                }),
                "is not '>=x.y.z <x.y.z'",
                False,
            ),
        ]

        failures = 0
        for title, archive, expected, should_pass in cases:
            problems = check_archive(archive)
            if should_pass:
                ok = not problems
                detail = "clean" if ok else "; ".join(problems)
            else:
                ok = any(expected in problem for problem in problems)
                detail = "; ".join(problems) or "no problem reported"
            print(f"[{'ok' if ok else 'fail'}] {title}: {detail}")
            failures += 0 if ok else 1
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    print(f"self-test: {len(cases)} cases, {failures} failure(s)")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    path = Path(argv[0])
    problems = check_archive(path)
    for problem in problems:
        print(f"[pack] {problem}")
    if problems:
        print(f"[pack] {path.name}: {len(problems)} problem(s)")
        return 1
    print(f"[pack] {path.name}: the envelope holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
