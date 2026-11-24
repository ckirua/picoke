"""Build picoke's extensions from Cython source."""

import sys
import platform
import pybind11

from Cython.Build import cythonize
from setuptools import Extension, setup

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
)
