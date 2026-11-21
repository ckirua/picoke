import base64
import binascii
from itertools import product

import pytest

from picore.crypto.base64 import (
    b64decode,
    b64decode_ascii,
    b64encode,
    b64encode_ascii,
)


@pytest.mark.parametrize(
    ("plain", "encoded"),
    [
        (b"", b""),
        (b"f", b"Zg=="),
        (b"fo", b"Zm8="),
        (b"foo", b"Zm9v"),
        (b"foob", b"Zm9vYg=="),
        (b"fooba", b"Zm9vYmE="),
        (b"foobar", b"Zm9vYmFy"),
        (b"\x00", b"AA=="),
        (b"\x00\x00\x00", b"AAAA"),
    ],
)
def test_rfc4648_encoding_and_decoding(plain: bytes, encoded: bytes) -> None:
    text = encoded.decode("ascii")
    assert b64encode(plain) == encoded
    assert b64encode_ascii(plain) == text
    assert b64decode(text) == plain
    assert b64decode_ascii(text) == plain


def test_encode_accepts_bytes_subclass() -> None:
    class EncodedBytes(bytes):
        pass

    value = EncodedBytes(b"foo")
    assert b64encode(value) == b"Zm9v"
    assert b64encode_ascii(value) == "Zm9v"


def test_decode_accepts_noncanonical_padding_bits() -> None:
    # Strict alphabet/padding validation does not require canonical pad bits.
    assert b64decode("Zh==") == b"f"
    assert b64decode_ascii("Zh==") == b"f"


@pytest.mark.parametrize("text", ["Zm9v!", "Zm9v/", "Zg=", "A===", "Zm=v"])
def test_invalid_base64_is_rejected(text: str) -> None:
    with pytest.raises((ValueError, binascii.Error)):
        b64decode(text)
    with pytest.raises((ValueError, binascii.Error)):
        b64decode_ascii(text)


def test_non_ascii_is_rejected() -> None:
    with pytest.raises((ValueError, binascii.Error)):
        b64decode("é")


@pytest.mark.parametrize(
    "value", [bytearray(b"foo"), memoryview(b"foo"), "foo", 1, None, True, [], object()]
)
def test_encode_rejects_non_bytes(value: object) -> None:
    with pytest.raises(TypeError):
        b64encode(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        b64encode_ascii(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [b"Zm9v", bytearray(b"Zm9v"), memoryview(b"Zm9v"), 1, None, True, [], object()],
)
def test_decode_rejects_non_strings(value: object) -> None:
    with pytest.raises(TypeError):
        b64decode(value)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        b64decode_ascii(value)  # type: ignore[arg-type]


def test_decode_preserves_str_subclass_encoding_behavior() -> None:
    class EncodedString(str):
        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            return b"YmFy"

    assert b64decode(EncodedString("Zm9v")) == b"bar"
    assert b64decode_ascii(EncodedString("Zm9v")) == b"bar"


def test_decode_preserves_virtual_str_instance_behavior() -> None:
    class StringProxy:
        __class__ = str

        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            return b"Zm9v"

    assert b64decode(StringProxy()) == b"foo"
    assert b64decode_ascii(StringProxy()) == b"foo"


def _assert_strict_decode_matches_stdlib(text: str) -> None:
    try:
        expected = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as expected_error:
        for decode in (b64decode, b64decode_ascii):
            with pytest.raises(type(expected_error)) as actual:
                decode(text)
            assert type(actual.value) is type(expected_error), repr(text)
            assert str(actual.value) == str(expected_error), repr(text)
    else:
        for decode in (b64decode, b64decode_ascii):
            actual = decode(text)
            assert type(actual) is bytes
            assert actual == expected, repr(text)


@pytest.mark.parametrize(
    "length",
    [
        *range(66),
        127, 128, 129,
        254, 255, 256, 257, 258,
        1023, 1024, 1025,
        4094, 4095, 4096, 4097, 4098,
        65534, 65535, 65536, 65537, 65538,
    ],
)
def test_binary_payloads_match_stdlib(length: int) -> None:
    # Exercise every byte value, embedded NULs, and trailing zero counts
    # independent of Base64's one- and two-byte padding.
    patterned = (bytes(range(256)) * ((length + 255) // 256))[:length]
    payloads = [patterned, bytes(length)]
    payloads.extend(
        patterned[: length - zeros] + bytes(zeros)
        for zeros in (1, 2, 3, 7)
        if zeros <= length
    )
    for payload in payloads:
        encoded = base64.b64encode(payload)
        text = encoded.decode("ascii")
        assert b64encode(payload) == encoded
        assert type(b64encode(payload)) is bytes
        assert b64encode_ascii(payload) == text
        assert type(b64encode_ascii(payload)) is str
        assert b64decode(text) == payload
        assert b64decode_ascii(text) == payload


@pytest.mark.parametrize("length", [2047, 2048, 2049])
def test_encode_near_dispatch_boundary_matches_stdlib(length: int) -> None:
    payload = (bytes(range(256)) * ((length + 255) // 256))[:length]
    encoded = base64.b64encode(payload)
    assert b64encode(payload) == encoded
    assert b64encode_ascii(payload) == encoded.decode("ascii")
    assert b64decode(encoded.decode("ascii")) == payload
    assert b64decode_ascii(encoded.decode("ascii")) == payload


@pytest.mark.parametrize("length", [60, 63, 64, 65, 68])
@pytest.mark.parametrize("kind", ["valid", "invalid", "padding", "trailing-nul"])
def test_decode_near_dispatch_boundary_matches_stdlib(
    length: int, kind: str
) -> None:
    if kind == "valid":
        valid_length = length - length % 4
        text = base64.b64encode(bytes((valid_length // 4) * 3)).decode("ascii")
    elif kind == "invalid":
        text = ("A" * length)[:-1] + "!"
    elif kind == "padding":
        text = ("A" * length)[:-2] + "=="
    else:
        text = ("A" * (length - 1)) + "\x00"
    _assert_strict_decode_matches_stdlib(text)


@pytest.mark.parametrize(
    "text",
    [
        "", "=", "==", "===", "====", "=====",
        "A", "AA", "AAA", "AAAA", "AAAAA",
        "AA=", "AA==", "AA===", "AA====",
        "AAA=", "AAA==", "AAA===", "AAAA=", "AAAA==", "AAAA====",
        "=AAAA", "A=AAA", "AA=AA", "AAA=A", "AA==A", "AAA=A=",
        "AA==AA==", "AAA=AAAA", "AAAA=AAAA", "AAAA====AAAA",
        "AA=!", "AA==!", "AAA=!", "AAAA=!",
        "Zh==", "Zv==", "Zm9=", "Zm/=", "//==", "///=",
        "Zg==\n", " Zg==", "Z g==", "Zg==\r\n", "Zg==\t",
        "Zm9v!", "Zm9v-", "Zm9v_", "Zm9v\x00", "\x00Zm9v",
        "\x7f", "é", "Zm9vé", "Zg==é", "\ud800", "😀",
    ],
)
def test_strict_decoding_matches_stdlib(text: str) -> None:
    _assert_strict_decode_matches_stdlib(text)


def test_short_alphabet_and_padding_combinations_match_stdlib() -> None:
    # Reduced alphabet exhaustively covers padding transitions, invalid
    # characters and incomplete quartets without nondeterministic fuzzing.
    for length in range(7):
        for characters in product("A/=\n", repeat=length):
            _assert_strict_decode_matches_stdlib("".join(characters))


def test_all_noncanonical_padding_bits_match_stdlib() -> None:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    for character in alphabet:
        _assert_strict_decode_matches_stdlib("A" + character + "==")
        _assert_strict_decode_matches_stdlib("AA" + character + "=")


def test_encode_uses_bytes_subclass_buffer_not_overrides() -> None:
    class EncodedBytes(bytes):
        def __bytes__(self) -> bytes:
            return b"different"

        def __len__(self) -> int:
            return 0

    value = EncodedBytes(b"\x00foo\x00\x00")
    expected = base64.b64encode(value)
    assert b64encode(value) == expected
    assert b64encode_ascii(value) == expected.decode("ascii")


def test_encode_preserves_virtual_bytes_buffer_behavior() -> None:
    class BytesBuffer(bytearray):
        __class__ = bytes

    value = BytesBuffer(b"\x00foo\x00\x00")
    expected = base64.b64encode(value)
    assert b64encode(value) == expected
    assert b64encode_ascii(value) == expected.decode("ascii")


def test_encode_rejects_virtual_bytes_without_buffer() -> None:
    class BytesProxy:
        __class__ = bytes

        def __bytes__(self) -> bytes:
            return b"foo"

    for encode in (b64encode, b64encode_ascii):
        with pytest.raises(TypeError):
            encode(BytesProxy())


@pytest.mark.parametrize(
    "encoded",
    [b"", b"AA==", b"AA==!", bytearray(b"Zm9v"), memoryview(b"Zm9v"), "Zm9v", None],
)
def test_decode_preserves_subclass_encode_result_behavior(encoded: object) -> None:
    class EncodedString(str):
        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> object:
            return encoded

    _assert_strict_decode_matches_stdlib(EncodedString("ignored"))


@pytest.mark.parametrize("text", ["\x00", "é", "\ud800"])
def test_decode_uses_str_subclass_encode_even_for_non_ascii_storage(text: str) -> None:
    class EncodedString(str):
        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            return b"AA=="

    _assert_strict_decode_matches_stdlib(EncodedString(text))


def test_decode_propagates_subclass_encode_exception() -> None:
    class EncodedString(str):
        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            raise RuntimeError("custom encode failed")

    for decode in (b64decode, b64decode_ascii):
        with pytest.raises(RuntimeError, match="^custom encode failed$"):
            decode(EncodedString("Zm9v"))


@pytest.mark.parametrize("size", [64 * 1024, 1024 * 1024])
@pytest.mark.parametrize("remainder", [0, 1, 2])
def test_large_binary_payloads_match_stdlib(size: int, remainder: int) -> None:
    length = size + remainder
    # A deterministic pattern includes embedded NULs; finish with NULs too.
    payload = (bytes(range(256)) * ((length + 255) // 256))[:length]
    payload = payload[:-3] + b"\0\0\0"
    expected = base64.b64encode(payload)
    text = expected.decode("ascii")

    encoded = b64encode(payload)
    ascii_encoded = b64encode_ascii(payload)
    assert type(encoded) is bytes
    assert type(ascii_encoded) is str
    assert encoded == expected
    assert ascii_encoded == text
    assert b64decode(text) == payload
    assert type(b64decode(text)) is bytes
    assert b64decode_ascii(text) == payload


@pytest.mark.parametrize("length", [4095, 4096, 4097, 16383, 16384, 16385])
def test_decode_across_block_boundaries_matches_stdlib(length: int) -> None:
    # Choose source lengths that cross output/block boundaries, including all
    # possible Base64 padding counts.
    payload = bytes((index * 37) & 0xFF for index in range(length))
    text = base64.b64encode(payload).decode("ascii")
    _assert_strict_decode_matches_stdlib(text)
    assert b64decode(text) == payload


@pytest.mark.parametrize("length", [2044, 2048, 2052, 4092, 4096, 4100])
def test_decode_near_simd_dispatch_boundary(length: int) -> None:
    # Complete quartets exercise both sides of the encoded-length threshold.
    _assert_strict_decode_matches_stdlib("AA/A" * (length // 4))
    _assert_strict_decode_matches_stdlib("AA/A" * (length // 4 - 1) + "AA==")
    _assert_strict_decode_matches_stdlib("AA/A" * (length // 4 - 1) + "AAA=")
    _assert_strict_decode_matches_stdlib("AA/A" * (length // 4 - 1) + "AA!=")


@pytest.mark.parametrize("character", ["é", "\ud800"])
def test_large_non_ascii_matches_stdlib(character: str) -> None:
    _assert_strict_decode_matches_stdlib("A" * 4096 + character)


def test_large_str_subclass_preserves_encode_override() -> None:
    class EncodedString(str):
        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            return b"Zm9v"

    _assert_strict_decode_matches_stdlib(EncodedString("A" * 4096))


@pytest.mark.parametrize("position", ["start", "middle", "end"])
@pytest.mark.parametrize("bad", ["!", " ", "="])
def test_large_late_errors_match_stdlib(position: str, bad: str) -> None:
    text = "A" * (64 * 1024)
    offset = {"start": 0, "middle": len(text) // 2, "end": len(text) - 1}[
        position
    ]
    malformed = text[:offset] + bad + text[offset + 1 :]
    _assert_strict_decode_matches_stdlib(malformed)


@pytest.mark.parametrize(
    "text",
    [
        "A" * 4095 + "!",
        "A" * 4096 + "!",
        "A" * 4097 + "!",
        "A" * 65532 + "A===",
        "A" * 65532 + "AA=A",
        "A" * 65532 + "AA==A",
        "A" * 65532 + "=AAA",
    ],
)
def test_large_invalid_alphabet_and_padding_match_stdlib(text: str) -> None:
    _assert_strict_decode_matches_stdlib(text)
