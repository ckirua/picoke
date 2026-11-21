"""Build picore's extensions from Cython source."""

from Cython.Build import cythonize
from setuptools import Extension, setup

modules = ("base64", "keccak", "ed25519", "secp256k1", "eip712", "bip137")
setup(
    ext_modules=cythonize(
        [Extension("picore.ratelimit", ["src/picore/ratelimit.pyx"])] + [
            Extension(
                f"picore.crypto.{module}",
                [f"src/picore/crypto/{module}.pyx"],
                libraries=["crypto"] if module in ("base64", "keccak") else [],
            )
            for module in modules
        ],
        build_dir="build/cython",
        compiler_directives={"language_level": 3, "annotation_typing": False},
    ),
)
