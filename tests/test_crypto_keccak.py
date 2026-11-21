import hashlib

import pytest

from picore.crypto.keccak import keccak256


@pytest.mark.parametrize(
    ("length", "expected_zero", "expected_pattern"),
    [
        (135, "29e3704feeca7fb9ba229f0fa04d9b36449cf3ad6e1d85d9cfff3a10df9abc3e", "cbdfd9dee5faad3818d6b06f95a219fd290b0e1706f6a82e5a595b9ce9faca62"),
        (136, "3a5912a7c5faa06ee4fe906253e339467a9ce87d533c65be3c15cb231cdb25f9", "7ce759f1ab7f9ce437719970c26b0a66ff11fe3e38e17df89cf5d29c7d7f807e"),
        (137, "bee7fbb405cb0d91a8775e338c4a5e4b5d6b2d051f687fa942043cffdc73bd28", "ac73d4fae68b8453f764007c1a20ce95994187861f0c3227a3a8e99a73a3b1db"),
        (271, "3bb611e98ca876adc01436a582979ecfd012389033aca7dbf76dcb424fe02a0c", "7c974895b2a88303ff2dc6b58f438ceb0b298cac91099ac0539cc0f477506191"),
        (272, "a8005c7a3125b6c3629b4181eca54d18721e41fef639718d205beb00b366ed7d", "fdf2ec49e749960d3c8521a0219af8d03e30e2b3bf19bd16150ee0eaf133d66e"),
    ],
)
def test_keccak256_rate_boundary_vectors(
    length: int, expected_zero: str, expected_pattern: str
) -> None:
    # Generated with @noble/hashes@1.8.0 keccak_256. The patterned input is
    # bytes(range(256))[:length], extended by repeating that range for lengths
    # above 256.
    pattern = (bytes(range(256)) * 2)[:length]
    assert keccak256(bytes(length)).hex() == expected_zero
    assert keccak256(pattern).hex() == expected_pattern


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
        (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
        (
            bytes(200),
            "e1bb54e1bc3af48d01e5dbfc81015c98152a574f6428c6948aa4837c9c0baad9",
        ),
    ],
)
def test_keccak256_vectors(data: bytes, expected: str) -> None:
    assert keccak256(data).hex() == expected


def test_keccak256_is_not_sha3_256() -> None:
    assert keccak256(b"") != hashlib.sha3_256(b"").digest()

def test_keccak256_accepts_bytes_subclasses() -> None:
    class BytesSubclass(bytes):
        pass

    assert keccak256(BytesSubclass(b"abc")).hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )


@pytest.mark.parametrize("data", [bytearray(b"abc"), memoryview(b"abc"), "abc", 123])
def test_keccak256_rejects_non_bytes(data: object) -> None:
    with pytest.raises(TypeError):
        keccak256(data)  # type: ignore[arg-type]


def test_keccak256_propagates_class_lookup_errors() -> None:
    class BrokenClass:
        @property
        def __class__(self) -> type:
            raise RuntimeError("class lookup failed")

    with pytest.raises(RuntimeError, match="class lookup failed"):
        keccak256(BrokenClass())  # type: ignore[arg-type]
