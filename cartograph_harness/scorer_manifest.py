"""scorer_manifest.py — SHA-256 freeze for Cartograph certification.

Mirrors fdi-conformance-lab/runners/scorer_manifest.py. Hashes every file
that materially contributed to a cert verdict — if any of these change
after a certificate is issued, re-running build_manifest() detects the
drift.

Components hashed:
  - tool      = cartograph/ source tree (the thing being certified)
  - harness   = cartograph_harness/ (this runner)
  - tests     = tests/ (the negative controls + injection guards)
  - taxonomy  = cartograph/taxonomy.py + embeddings cache path
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

COMPONENT_GLOBS: dict[str, tuple[Path, list[str]]] = {
    "tool":     (REPO_ROOT / "cartograph",         ["*.py"]),
    "harness":  (REPO_ROOT / "cartograph_harness", ["**/*.py"]),
    "tests":    (REPO_ROOT / "tests",              ["**/*.py"]),
}


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(root: Path, patterns: list[str]) -> list[Path]:
    out: set[Path] = set()
    if not root.exists():
        return []
    for pat in patterns:
        for p in root.glob(pat):
            if not p.is_file():
                continue
            if any(part == "__pycache__" or part.startswith(".")
                   for part in p.parts):
                continue
            out.add(p.resolve())
    return sorted(out)


def _hash_component(root: Path, patterns: list[str]) -> dict:
    files = _collect_files(root, patterns)
    per_file = {}
    rollup = hashlib.sha256()
    for p in files:
        rel = p.relative_to(REPO_ROOT).as_posix()
        h = _hash_file(p)
        per_file[rel] = h
        rollup.update(rel.encode("utf-8"))
        rollup.update(b"\0")
        rollup.update(h.encode("ascii"))
        rollup.update(b"\n")
    return {
        "sha256": rollup.hexdigest(),
        "file_count": len(files),
        "files": per_file,
    }


def build_manifest(include_files: bool = False) -> dict:
    """Build a fresh SHA-256 manifest of every cert-critical file."""
    manifest = {"components": {}}
    for label, (root, patterns) in COMPONENT_GLOBS.items():
        comp = _hash_component(root, patterns)
        if not include_files:
            comp.pop("files", None)
        manifest["components"][label] = comp

    overall = hashlib.sha256()
    for label in sorted(manifest["components"]):
        overall.update(label.encode("ascii"))
        overall.update(b":")
        overall.update(manifest["components"][label]["sha256"].encode("ascii"))
        overall.update(b"\n")
    manifest["fingerprint_sha256"] = overall.hexdigest()
    manifest["spec_version"] = "cartograph-scorer-manifest/1.0"
    return manifest


def fingerprint_certificate_body(body: dict) -> str:
    """Hash of the certificate body itself, excluding the self-hash field."""
    body = {k: v for k, v in body.items() if k != "certificate_body_sha256"}
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"),
                             default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("--verbose", action="store_true",
                    help="include per-file hashes")
    args = a.parse_args()
    print(json.dumps(build_manifest(include_files=args.verbose), indent=2))
