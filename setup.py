"""Build picoke's extensions from Cython source."""

import sys
import os
import platform
import pybind11

from Cython.Build import cythonize
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


_test_hooks = os.environ.get("PICOKE_IPC_TEST_HOOKS") == "1"


class _BuildExt(build_ext):
    """Rebuild the atomic IPC extension when its test-hook mode changes."""

    def build_extension(self, extension):
        if extension.name != "picoke._atomic_ipc_native":
            return super().build_extension(extension)

        marker = Path(self.build_temp) / "picoke-ipc-test-hooks"
        previous = marker.read_text() if marker.exists() else None
        changed = previous != ("1" if _test_hooks else "0")
        force = self.force
        if changed:
            self.force = True
        try:
            super().build_extension(extension)
        finally:
            self.force = force
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1" if _test_hooks else "0")

modules = ("base64", "keccak", "ed25519", "secp256k1", "eip712", "bip137")
setup(
    ext_modules=cythonize(
        [
            Extension("picoke.queues", ["src/picoke/queues.pyx"]),
            Extension("picoke.ratelimit", ["src/picoke/ratelimit.pyx"]),
            Extension("picoke._binary_logging_native", ["src/picoke/_binary_logging_native.pyx"]),
        ] + ([
            Extension(
                "picoke._ipc_native",
                ["src/picoke/_ipc_native.cpp"],
                include_dirs=[pybind11.get_include()],
                language="c++",
                extra_compile_args=["-std=c++17"],
            ),
        ] + ([
            Extension(
                "picoke._atomic_ipc_native",
                ["src/picoke/_atomic_ipc.cpp"],
                include_dirs=[pybind11.get_include()],
                language="c++",
                extra_compile_args=["-std=c++17"],
                define_macros=[("PICOKE_IPC_TEST_HOOKS", "1")] if _test_hooks else [],
            )
        ] if platform.machine() in ("x86_64", "AMD64", "amd64") else []) if sys.platform == "linux" else []) + [
            Extension(
                f"picoke.crypto.{module}",
                [f"src/picoke/crypto/{module}.pyx"],
                libraries=["crypto"] if module in ("base64", "keccak") else [],
            )
            for module in modules
        ],
        build_dir="build/cython",
        compiler_directives={"language_level": 3, "annotation_typing": False},
    ),
    cmdclass={"build_ext": _BuildExt},
)
