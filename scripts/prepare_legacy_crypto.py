#!/usr/bin/env python3
"""Fetch and build selected legacy crypto extensions in an isolated package.

Run with an archive URL or local tarball supplied by the user:

    python scripts/prepare_legacy_crypto.py --archive PATH_OR_URL --source-root PACKAGE_DIR --output PATH

The output directory must not already exist.
"""


from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

FILES = (
    "crypto/encoding/base64.pyx",
    "crypto/encoding/base64.pxd",
    "crypto/hashes/keccak.pyx",
    "crypto/hashes/keccak.pxd",
    "crypto/curves/ed25519.pyx",
    "crypto/curves/ed25519.pxd",
    "crypto/hashes/__init__.pxd",
    "crypto/hashes/sha256.pyx",
    "crypto/hashes/sha256.pxd",
    "crypto/hashes/hmac.pyx",
    "crypto/hashes/hmac.pxd",
    "crypto/curves/__init__.pxd",
    "crypto/curves/secp256k1.pyx",
    "crypto/curves/secp256k1.pxd",
    "crypto/signing/eip712.pyx",
    "crypto/signing/eip712.pxd",
    "crypto/signing/bip137.pyx",
    "crypto/signing/bip137.pxd",
    "crypto/openssl.pxd",
)
SUBPACKAGES = ("", "crypto", "crypto/encoding", "crypto/hashes", "crypto/curves", "crypto/signing")


def log(message: str) -> None:
    print(f"[legacy-crypto] {message}", flush=True)

def fetch_snapshot(destination: Path, archive_location: str, source_root: str, package: str) -> None:
    prefix_path = source_root + "/"
    log(f"Reading source archive from {archive_location}")
    archive_path = Path(archive_location).expanduser()
    if archive_path.is_file():
        archive_file_context = archive_path.open("rb")
    else:
        request = urllib.request.Request(
            archive_location, headers={"User-Agent": "picore-legacy-bench"}
        )
        archive_file_context = urllib.request.urlopen(request)
    with archive_file_context as downloaded, tempfile.TemporaryFile() as archive_file:
        shutil.copyfileobj(downloaded, archive_file)
        archive_file.seek(0)
        with tarfile.open(fileobj=archive_file, mode="r:gz") as archive:
            members = archive.getmembers()
            first_file = prefix_path + FILES[0]
            prefixes = {
                member.name[:-len(first_file)] for member in members
                if member.name == first_file or member.name.endswith("/" + first_file)
            }
            if len(prefixes) != 1:
                raise RuntimeError("Expected one matching source root in the archive")
            prefix, = prefixes
            wanted = {prefix_path + name for name in FILES}
            wanted.add("python/LICENSE")
            for member in members:
                relative = member.name.removeprefix(prefix)
                if relative not in wanted:
                    continue
                if not member.isfile():
                    raise RuntimeError(f"Unexpected archive entry: {relative}")
                target = destination / (
                    Path("LICENSE") if relative == "python/LICENSE"
                    else Path(package) / Path(relative.removeprefix(prefix_path))
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Cannot read archive entry: {relative}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            missing = [name for name in FILES if not (destination / package / name).is_file()]
            if missing or not (destination / "LICENSE").is_file():
                raise RuntimeError(f"Source archive missing required files: {missing}")


def write_build_files(root: Path, package: str) -> None:
    packages = [package + ("/" + name if name else "") for name in SUBPACKAGES]
    for name in packages:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("\"\"\"Focused legacy crypto benchmark package.\"\"\"\n")
    (root / "PROVENANCE.txt").write_text(
        "Source: user-supplied archive.\n"
        "Selected source files and adjacent declarations are unmodified. Minimal\n"
        "package initializers and setup.py are generated for this isolated benchmark\n"
        "build. See LICENSE for the source distribution's license.\n"
    )
    (root / "setup.py").write_text(
        "from setuptools import Extension, setup\n"
        "from Cython.Build import cythonize\n"
        "from pathlib import Path\n"
        "root = Path(__file__).parent\n"
        f"package = {package!r}\n"
        f"packages = {[name.replace('/', '.') for name in packages]!r}\n"
        f"sources = {[name for name in FILES if name.endswith('.pyx')]!r}\n"
        "modules = [Extension(package + '.' + name[:-4].replace('/', '.'),\n"
        "                     [str(root / package / name)],\n"
        "                     include_dirs=[str(root / package)], libraries=['crypto'])\n"
        "           for name in sources]\n"
        "setup(name='legacy-crypto-benchmark', version='0', packages=packages,\n"
        "      ext_modules=cythonize(modules, build_dir=str(root / 'cython-build'),\n"
        "                            compiler_directives={'language_level': 3}))\n"
    )




def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="user-supplied local .tar.gz archive or archive URL")
    parser.add_argument("--source-root", required=True,
                        help="archive-relative package directory, e.g. python/src/legacy_package")
    parser.add_argument("--output", required=True, type=Path, help="new output directory (must not already exist)")
    args = parser.parse_args()
    source_root = args.source_root.strip("/")
    package = source_root.rsplit("/", 1)[-1]
    if not package.isidentifier():
        parser.error("--source-root must end in a Python package name")
    output = args.output.expanduser().resolve()
    if output == Path(output.anchor) or output == Path.cwd().resolve():
        parser.error("--output must not be a filesystem root or the current directory")
    if output.exists():
        parser.error("--output already exists; choose a new directory")
    output.mkdir(parents=True)
    try:
        fetch_snapshot(output, args.archive, source_root, package)
        write_build_files(output, package)
        log(f"Building extensions with {sys.executable}; requires Cython declarations and OpenSSL libcrypto")
        subprocess.run([sys.executable, "setup.py", "build_ext", "--inplace"], cwd=output, check=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        log(f"ERROR: {error}")
        return 1
    log(f"Build complete: {output}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
