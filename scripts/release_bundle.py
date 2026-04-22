#!/usr/bin/env python3
"""Create release assets for the source-first clone plugin."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
from pathlib import Path
from tarfile import TarInfo


PLUGIN_NAME = "source-first-clone"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_filter(member: TarInfo) -> TarInfo | None:
    parts = Path(member.name).parts
    if "__pycache__" in parts or member.name.endswith(".pyc"):
        return None
    return member


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    dist_root = repo_root / "dist"
    bundle_root = repo_root / "bundle" / PLUGIN_NAME
    installer = repo_root / "python" / "web_embedding" / "installer.py"
    bootstrap = repo_root / "scripts" / "bootstrap.sh"

    if dist_root.exists():
        shutil.rmtree(dist_root)
    dist_root.mkdir(parents=True, exist_ok=True)

    bundle_archive = dist_root / f"{PLUGIN_NAME}-bundle.tar.gz"
    with tarfile.open(bundle_archive, "w:gz") as archive:
        archive.add(bundle_root, arcname=PLUGIN_NAME, filter=release_filter)

    install_py = dist_root / "install.py"
    install_sh = dist_root / "install.sh"
    shutil.copy2(installer, install_py)
    shutil.copy2(bootstrap, install_sh)

    checksums = {
        bundle_archive.name: sha256(bundle_archive),
        install_py.name: sha256(install_py),
        install_sh.name: sha256(install_sh),
    }

    with (dist_root / "SHA256SUMS").open("w") as handle:
        for filename, digest in checksums.items():
            handle.write(f"{digest}  {filename}\n")

    print(f"Release assets written to {dist_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
