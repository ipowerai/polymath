#!/usr/bin/env python3
"""Builds dist/polymath.zip, the one download for macOS, Windows and Linux.

Only the files a learner needs go in, by an explicit list, and the build stops if one looks like a secret or
personal data file. Run it from anywhere: python3 packaging/build_release.py
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FOLDERS = ("blueprints", "references", "scripts", "skills", "web", ".claude-plugin")
FILES = ("LICENSE", "NOTICE", "README.md", "install.sh", "install.ps1")
LEFT_OUT = ("__pycache__", ".DS_Store")
NEVER = re.compile(r"(^|/)(\.env[^/]*|web-token|web-auth\.json|models\.json|events\.jsonl|learner\.(json|md))$")
SECRET = re.compile(rb"sk-ant-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY")
STAMP = (2026, 1, 1, 0, 0, 0)  # the same files always make the same zip


def members(root: Path = REPO) -> list:
    found = [root / name for name in FILES]
    for folder in FOLDERS:
        found += [p for p in (root / folder).rglob("*") if p.is_file() and not set(p.parts) & set(LEFT_OUT)]
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def build(out: Path = REPO / "dist" / "polymath.zip", root: Path = REPO) -> dict:
    version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".tmp")
    names = []
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in members(root):
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            if NEVER.search(name):
                raise SystemExit(f"refusing to package {name}")
            if SECRET.search(data):
                raise SystemExit(f"{name} contains something that looks like a secret")
            info = zipfile.ZipInfo(f"polymath/{name}", STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | (0o755 if name == "install.sh" else 0o644)) << 16
            bundle.writestr(info, data)
            names.append(name)
    temp.replace(out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_name(out.name + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    return {"version": version, "path": str(out), "files": len(names), "bytes": out.stat().st_size, "sha256": digest,
            "names": names}


if __name__ == "__main__":
    result = build()
    print(f"Polymath {result['version']}: {result['path']}")
    print(f"{result['files']} files, {result['bytes'] / 1024:.0f} KB, sha256 {result['sha256']}")
    sys.exit(0)
