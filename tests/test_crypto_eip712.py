import json
import subprocess
import sys

import pytest

from picore.crypto.eip712 import eip712_hash_agent_message, eip712_hash_full_message


MAIL = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Person": [
            {"name": "name", "type": "string"},
            {"name": "wallet", "type": "address"},
        ],
        "Mail": [
            {"name": "from", "type": "Person"},
            {"name": "to", "type": "Person"},
            {"name": "contents", "type": "string"},
        ],
    },
    "primaryType": "Mail",
    "domain": {
        "name": "Ether Mail",
        "version": "1",
        "chainId": 1,
        "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
    },
    "message": {
        "from": {"name": "Cow", "wallet": "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826"},
        "to": {"name": "Bob", "wallet": "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"},
        "contents": "Hello, Bob!",
    },
}


def test_account_provider_loads_only_for_generic_encoding(tmp_path) -> None:
    subprocess.run(
        [sys.executable, "-I", "-c", """
import json
import sys
from picore import crypto

domain = {"name": "Agent", "version": "1", "chainId": 42161,
          "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"}
expected = "b4213747a19486f4f2ceb0f7222b9733f612682fd3649afd6a605a21ac36f2da"
def agent_digest():
    return crypto.eip712_hash_agent_message(domain, "wss://example.test", bytes(range(32))).hex()

assert agent_digest() == expected
try:
    crypto.eip712_hash_full_message({})
except KeyError as error:
    assert error.args == ("types",)
else:
    raise AssertionError("missing envelope fields were accepted")
assert "eth_account" not in sys.modules

domain["chainId"] = "42161"
assert agent_digest() == expected
assert "eth_account.messages" in sys.modules
assert crypto.eip712_hash_full_message(json.loads(sys.argv[1])).hex() == (
    "be609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"
)
""", json.dumps(MAIL)],
        cwd=tmp_path,
        check=True,
    )


def test_official_eip712_mail_vector() -> None:
    message = MAIL
    assert eip712_hash_full_message(message).hex() == (
        "be609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"
    )


def test_nested_struct_and_array_fields_are_hashed() -> None:
    typed = {
        "types": {
            "EIP712Domain": [],
            "Item": [{"name": "value", "type": "uint256"}],
            "Batch": [{"name": "items", "type": "Item[]"}],
        },
        "primaryType": "Batch",
        "domain": {},
        "message": {"items": [{"value": 1}, {"value": 2}]},
    }
    original = eip712_hash_full_message(typed)
    typed["message"]["items"][1]["value"] = 3
    assert len(original) == 32
    assert eip712_hash_full_message(typed) != original


def test_agent_hash_matches_equivalent_full_typed_message() -> None:
    domain = {"name": "Agent", "version": "1", "chainId": 42161,
              "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"}
    source, connection_id = "wss://example.test", bytes(range(32))
    equivalent = _agent_envelope(domain, source, connection_id)
    assert eip712_hash_agent_message(domain, source, connection_id) == eip712_hash_full_message(equivalent)


def _agent_envelope(domain: dict, source: str, connection_id: bytes) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
        },
        "primaryType": "Agent",
        "domain": domain,
        "message": {"source": source, "connectionId": connection_id},
    }


def _outcome(callable_, *args: object) -> tuple:
    try:
        return ("result", callable_(*args))
    except Exception as error:
        return ("error", type(error), str(error))


@pytest.mark.parametrize(
    "domain,source,expected_kind",
    [
        ({"name": "Agent", "version": "1", "chainId": "42161",
          "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"}, "π snowman ☃", "result"),
        ({"name": "Agent", "version": "1", "chainId": 42161,
          "verifyingContract": "0xnot-an-address"}, "source", "error"),
        ({"name": "Agent", "version": "1", "chainId": 42161,
          "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC", "extra": 1}, "source", "error"),
        ({"name": "Agent", "version": "1", "chainId": None,
          "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"}, "source", "error"),
    ],
)
def test_agent_matches_full_message_validation(domain: dict, source: str, expected_kind: str) -> None:
    connection_id = bytes(32)
    agent = _outcome(eip712_hash_agent_message, domain, source, connection_id)
    full = _outcome(eip712_hash_full_message, _agent_envelope(domain, source, connection_id))
    assert agent == full
    assert agent[0] == expected_kind


def test_agent_missing_domain_values_follow_declared_order() -> None:
    domain = {}
    agent = _outcome(eip712_hash_agent_message, domain, "source", bytes(32))
    full = _outcome(eip712_hash_full_message, _agent_envelope(domain, "source", bytes(32)))
    assert agent == full == ("error", ValueError, "Missing value for domain field 'name'")


@pytest.mark.parametrize(
    "domain,source,connection_id,expected",
    [
        ([], "source", bytes(32), ("error", TypeError, "domain must be a dict")),
        ({}, 1, bytes(32), ("error", TypeError, "source must be a str")),
        ({}, "source", bytes(31), ("error", ValueError, "connection_id must be exactly 32 bytes")),
    ],
)
def test_agent_outer_checks_precede_domain_validation(
    domain: object, source: object, connection_id: object, expected: tuple
) -> None:
    assert _outcome(eip712_hash_agent_message, domain, source, connection_id) == expected


@pytest.mark.parametrize("domain", [
    {"name": "Agent", "version": "1", "chainId": 1, "verifyingContract": "0x" + "00" * 20},
    {"name": "Agent", "version": "1", "chainId": 1},
    {"name": "Agent", "version": "1", "chainId": 1, "verifyingContract": None},
])
def test_agent_dict_subclass_dispatch_matches_full_message(domain: dict) -> None:
    class TrackingDict(dict):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.lookups: list[tuple[str, object]] = []

        def __contains__(self, key: object) -> bool:
            self.lookups.append(("contains", key))
            return super().__contains__(key)

        def __getitem__(self, key: object) -> object:
            self.lookups.append(("getitem", key))
            return super().__getitem__(key)

    agent_domain = TrackingDict(domain)
    full_domain = TrackingDict(domain)
    agent = _outcome(eip712_hash_agent_message, agent_domain, "source", bytes(32))
    full = _outcome(eip712_hash_full_message, _agent_envelope(full_domain, "source", bytes(32)))
    assert agent == full
    assert agent_domain.lookups == full_domain.lookups
    assert ("getitem", "name") in agent_domain.lookups


@pytest.mark.parametrize(
    "domain,source,connection_id,expected",
    [
        (
            {"name": "", "version": "", "chainId": 0,
             "verifyingContract": "0x" + "00" * 19 + "01"},
            "", bytes.fromhex("80" + "00" * 30 + "01"),
            "3a32eee50f60683ef6310bff92342c7d8099555e8e2160274c6fa5d0716ca404",
        ),
        (
            {"verifyingContract": "0x" + "00" * 19 + "01",
             "chainId": 2**255 + 0x1234, "version": "雪☃", "name": "π"},
            "π snowman ☃", bytes.fromhex("ff" + "12" * 30 + "34"),
            "cde6aff2d9d5753148143392460f67769a82934652201ce1f5d2502b77176709",
        ),
        (
            {"name": "max", "version": "v", "chainId": 2**256 - 1,
             "verifyingContract": "0x" + "00" * 18 + "AB" + "00"},
            "source", bytes.fromhex("01" + "23" * 30 + "fe"),
            "fd53ce221c58ba5b8e9033c95f64ef6aedc4e1babdcb2a3b0bda19fbe1d51e96",
        ),
    ],
)
def test_agent_fixed_schema_matches_generic_for_boundary_values(
    domain: dict, source: str, connection_id: bytes, expected: str
) -> None:
    # ethers@6.15.0 TypedDataEncoder.hash reference digests.
    assert eip712_hash_agent_message(domain, source, connection_id).hex() == expected
    assert eip712_hash_full_message(_agent_envelope(domain, source, connection_id)).hex() == expected


def test_agent_fixed_schema_does_not_reuse_previous_call_data() -> None:
    first = ({"name": "first", "version": "1", "chainId": 0,
              "verifyingContract": "0x" + "00" * 20}, "", bytes(32))
    second = ({"name": "second", "version": "2", "chainId": 2**255 + 7,
               "verifyingContract": "0x" + "00" * 19 + "01"},
              "changed ☃", bytes.fromhex("fe" + "11" * 30 + "01"))
    for domain, source, connection_id in (first, second, first):
        assert _outcome(eip712_hash_agent_message, domain, source, connection_id) == _outcome(
            eip712_hash_full_message, _agent_envelope(domain, source, connection_id)
        )


@pytest.mark.parametrize(
    "name,version,chain_id,source,connection_id",
    [
        ("", "0", 0, "", bytes(32)),
        ("Agent", "1", 42161, "a", bytes(range(32))),
        ("π", "雪☃", 2**128 + 17, "π snowman ☃", bytes(reversed(range(32)))),
        ("x" * 31, "v" * 32, 2**256 - 1, "y" * 33, b"\xff" * 32),
    ],
)
def test_agent_bounded_deterministic_corpus_matches_full_message(
    name: str, version: str, chain_id: int, source: str, connection_id: bytes
) -> None:
    domain = {
        "name": name,
        "version": version,
        "chainId": chain_id,
        "verifyingContract": "0x" + "12" * 20,
    }
    assert eip712_hash_agent_message(domain, source, connection_id) == eip712_hash_full_message(
        _agent_envelope(domain, source, connection_id)
    )


def test_agent_hash_preserves_nul_and_unicode_normalization() -> None:
    domain = {"name": "Agent", "version": "1", "chainId": 1,
              "verifyingContract": "0x" + "12" * 20}
    connection_id = bytes(32)
    digests = set()
    for source in ("é\0tail", "e\u0301\0tail", "é"):
        digest = eip712_hash_agent_message(domain, source, connection_id)
        assert digest == eip712_hash_full_message(_agent_envelope(domain, source, connection_id))
        digests.add(digest)
    assert len(digests) == 3


@pytest.mark.parametrize("byte_length", [0, 1, 31, 32, 135, 136, 137, 271, 272, 273])
def test_agent_keccak_utf8_rate_boundaries_match_full_message(byte_length: int) -> None:
    # Each snowman occupies three UTF-8 bytes; the final ASCII padding reaches
    # the requested byte length without splitting a code point.
    source = "☃" * (byte_length // 3) + "a" * (byte_length % 3)
    domain = {
        "name": "Agent",
        "version": "1",
        "chainId": 1,
        "verifyingContract": "0x" + "34" * 20,
    }
    assert len(source.encode("utf-8")) == byte_length
    assert eip712_hash_agent_message(domain, source, bytes(32)) == eip712_hash_full_message(
        _agent_envelope(domain, source, bytes(32))
    )


def test_agent_same_domain_dict_mutations_match_full_message() -> None:
    domain = {
        "name": "first",
        "version": "1",
        "chainId": 1,
        "verifyingContract": "0x" + "56" * 20,
    }
    connection_id = bytes(32)
    for name, chain_id, source in (
        ("first", 1, "initial"),
        ("second ☃", 2**255, "changed π"),
        ("", 2**256 - 1, "z" * 137),
    ):
        domain["name"] = name
        domain["chainId"] = chain_id
        expected = eip712_hash_full_message(_agent_envelope(domain, source, connection_id))
        assert eip712_hash_agent_message(domain, source, connection_id) == expected


@pytest.mark.parametrize(
    "domain,source,connection_id",
    [
        ({"name": "A", "version": "1", "chainId": True,
          "verifyingContract": "0x" + "00" * 20}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": -1,
          "verifyingContract": "0x" + "00" * 20}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": 2**256,
          "verifyingContract": "0x" + "00" * 20}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": "0x10",
          "verifyingContract": "0x" + "00" * 20}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": 1,
          "verifyingContract": "0x" + "00" * 19 + "é"}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": 1,
          "verifyingContract": "0x" + "00" * 19 + "\ud800"}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": 1,
          "verifyingContract": "0x" + "00" * 20, "extra": 1}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": 1}, "s", bytes(32)),
        ({"name": "A", "version": "1", "chainId": 1,
          "verifyingContract": "0x" + "00" * 20}, "\ud800", bytes(32)),
    ],
)
def test_agent_fixed_schema_fallback_matches_generic_errors(
    domain: dict, source: str, connection_id: bytes
) -> None:
    agent = _outcome(eip712_hash_agent_message, domain, source, connection_id)
    generic = _outcome(eip712_hash_full_message, _agent_envelope(domain, source, connection_id))
    assert agent == generic


def test_agent_exact_type_dispatch_matches_generic_for_subclasses() -> None:
    class Text(str):
        pass

    class Blob(bytes):
        pass

    class Domain(dict):
        pass

    values = {"name": "Agent", "version": "1", "chainId": 10,
              "verifyingContract": "0x" + "00" * 20}
    cases = [
        (Domain(values), "source", bytes(32)),
        (values, Text("source"), bytes(32)),
        (values, "source", Blob(bytes(32))),
    ]
    for domain, source, connection_id in cases:
        assert _outcome(eip712_hash_agent_message, domain, source, connection_id) == _outcome(
            eip712_hash_full_message, _agent_envelope(domain, source, connection_id)
        )


def test_agent_nonexact_domain_key_preserves_equality_dispatch() -> None:
    class TrackingKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other: object) -> bool:
            comparisons.append(other)
            return super().__eq__(other)

    comparisons: list[object] = []
    domain = {TrackingKey("name"): "Agent", "version": "1", "chainId": 10,
              "verifyingContract": "0x" + "00" * 20}
    agent = eip712_hash_agent_message(domain, "source", bytes(32))
    agent_comparisons = comparisons.copy()
    comparisons.clear()
    assert agent == eip712_hash_full_message(_agent_envelope(domain, "source", bytes(32)))
    assert agent_comparisons == comparisons
    assert "name" in comparisons


@pytest.mark.parametrize("bad", [None, [], "message", 1])
def test_full_message_rejects_non_dict(bad: object) -> None:
    with pytest.raises(TypeError):
        eip712_hash_full_message(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("connection_id", [b"short", bytes(33), bytearray(32), memoryview(bytes(32))])
def test_agent_rejects_wrong_connection_id(connection_id: object) -> None:
    domain = {"name": "Agent", "version": "1", "chainId": 1,
              "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"}
    with pytest.raises((TypeError, ValueError)):
        eip712_hash_agent_message(domain, "source", connection_id)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "message",
    [
        {"types": {}, "primaryType": "Missing", "domain": {}, "message": {}},
        {"types": {"EIP712Domain": [], "Data": [{"name": "n", "type": "uint256"}]},
         "primaryType": "Data", "domain": {}, "message": {"n": "not-an-integer"}},
    ],
)
def test_full_message_rejects_invalid_typed_data(message: dict) -> None:
    with pytest.raises((ValueError, TypeError, KeyError)):
        eip712_hash_full_message(message)


def test_agent_rejects_bad_source_and_domain_types() -> None:
    domain = {}
    with pytest.raises(TypeError):
        eip712_hash_agent_message(domain, 4, bytes(32))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        eip712_hash_agent_message([], "source", bytes(32))  # type: ignore[arg-type]


def test_full_message_ignores_unrelated_type_definitions() -> None:
    message = {**MAIL, "types": {
        **MAIL["types"],
        "Unused": [{"name": "notUsed", "type": "UnsupportedType"}],
    }}
    assert eip712_hash_full_message(message) == eip712_hash_full_message(MAIL)


@pytest.mark.parametrize("bad_value", [None])
def test_full_message_rejects_none_nested_members(bad_value: object) -> None:
    message = {**MAIL, "message": {
        **MAIL["message"],
        "to": {**MAIL["message"]["to"], "name": bad_value},
    }}
    with pytest.raises(ValueError):
        eip712_hash_full_message(message)


def test_full_message_rejects_absent_nested_members() -> None:
    person = {**MAIL["message"]["to"]}
    del person["name"]
    message = {**MAIL, "message": {**MAIL["message"], "to": person}}
    with pytest.raises(ValueError):
        eip712_hash_full_message(message)


def test_full_message_rejects_wrong_fixed_array_length() -> None:
    typed = {
        "types": {
            "EIP712Domain": [],
            "Batch": [{"name": "values", "type": "uint256[2]"}],
        },
        "primaryType": "Batch",
        "domain": {},
        "message": {"values": [1]},
    }
    with pytest.raises(ValueError):
        eip712_hash_full_message(typed)


@pytest.mark.parametrize(
    ("name", "type_name"),
    [("name", "bytes32"), ("chainId", "uint8"), ("unknown", "string")],
)
def test_full_message_rejects_noncanonical_domain_fields(name: str, type_name: str) -> None:
    typed = {
        "types": {
            "EIP712Domain": [{"name": name, "type": type_name}],
            "Data": [{"name": "value", "type": "uint256"}],
        },
        "primaryType": "Data",
        "domain": {name: bytes(32) if type_name == "bytes32" else 1},
        "message": {"value": 1},
    }
    with pytest.raises(ValueError):
        eip712_hash_full_message(typed)


def test_full_message_preserves_dict_subclass_dispatch() -> None:
    class TrackingDict(dict):
        contains_calls = 0

        def __contains__(self, key: object) -> bool:
            type(self).contains_calls += 1
            return super().__contains__(key)

    message = {**MAIL, "message": TrackingDict(MAIL["message"])}
    assert eip712_hash_full_message(message) == eip712_hash_full_message(MAIL)
    assert TrackingDict.contains_calls > 0


def test_full_message_recursive_validation_keeps_python_recursion_guard() -> None:
    import sys

    original_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(200)
        typed = {
            "types": {
                "EIP712Domain": [],
                "Node": [{"name": "next", "type": "Node"}],
            },
            "primaryType": "Node",
            "domain": {},
            "message": {},
        }
        node = typed["message"]
        for _ in range(250):
            child = {}
            node["next"] = child
            node = child
        with pytest.raises(RecursionError):
            eip712_hash_full_message(typed)

        node["next"] = typed["message"]
        with pytest.raises(RecursionError):
            eip712_hash_full_message(typed)
    finally:
        sys.setrecursionlimit(original_limit)


def test_tuple_arrays_normalize_without_mutating_and_match_ethers_vector() -> None:
    # ethers@6.15.0 TypedDataEncoder.hash reference; Item[] with uint256 values.
    base = {
        "types": {
            "EIP712Domain": [],
            "Item": [{"name": "v", "type": "uint256"}],
            "Data": [{"name": "items", "type": "Item[]"}],
        },
        "primaryType": "Data",
        "domain": {},
    }
    typed = {**base, "message": {"items": ({"v": 1}, {"v": 2})}}
    original = typed["message"]["items"]
    assert eip712_hash_full_message(typed).hex() == (
        "076a90553e534f54d979119bcbfd7ef5fdf0c10871ca96c5aaad45e4f783618d"
    )
    typed["message"] = {"items": [{"v": 1}, {"v": 2}]}
    assert eip712_hash_full_message(typed).hex() == (
        "076a90553e534f54d979119bcbfd7ef5fdf0c10871ca96c5aaad45e4f783618d"
    )
    assert original == ({"v": 1}, {"v": 2})


@pytest.mark.parametrize("fields", [
    [{"name": "name", "type": "string"}, {"name": "name", "type": "string"}],
    [{"name": "version", "type": "string"}, {"name": "name", "type": "string"}],
])
def test_domain_fields_must_be_unique_and_canonical_order(fields: list[dict]) -> None:
    typed = {
        "types": {"EIP712Domain": fields, "Data": [{"name": "value", "type": "uint256"}]},
        "primaryType": "Data", "domain": {"name": "x", "version": "1"}, "message": {"value": 1},
    }
    with pytest.raises(ValueError):
        eip712_hash_full_message(typed)


def test_mutually_recursive_array_graph_keeps_provider_primary_type_error() -> None:
    # ethers/eth-account cannot infer a primary type for mutually recursive graphs.
    typed = {
        "types": {
            "EIP712Domain": [],
            "A": [{"name": "bs", "type": "B[]"}],
            "B": [{"name": "as_", "type": "A[]"}],
        },
        "primaryType": "A", "domain": {}, "message": {"bs": [{"as_": []}]},
    }
    with pytest.raises(ValueError):
        eip712_hash_full_message(typed)

@pytest.mark.parametrize(
    ("types", "primary", "domain", "message", "expected"),
    [
        ({"Data": [{"name": "xs", "type": "uint256[]"}]}, "Data", {}, {"xs": []},
         "d4c299d092c5d35c5baf09a8167b94b4be512458da5ad90bfaa80d37334202f1"),
        ({"Data": [{"name": "xs", "type": "uint256[]"}]}, "Data", {}, {"xs": [1, 2]},
         "d081986cf54a7c1a88a39f5b198d99942aea2ca40e525a5e1b0cffa55e52737b"),
        ({"Inner": [{"name": "v", "type": "string"}],
          "Data": [{"name": "grid", "type": "Inner[2][]"}]}, "Data", {},
         {"grid": [[{"v": "é"}, {"v": "雪"}]]},
         "799e43c3e3a43b516a89e12113ace1f045aa2ee06639eeefc1ae8c2a898f6cb8"),
        ({"Person": [{"name": "name", "type": "string"}, {"name": "wallet", "type": "address"}],
          "Data": [{"name": "person", "type": "Person"}]}, "Data", {},
         {"person": {"name": "Zoë", "wallet": "0x0000000000000000000000000000000000000001"}},
         "491335c38ebdb48835bb2e5274ceb8baa9fc6e5be946c5c2fe9b22111ab10108"),
        ({"Data": [{"name": "text", "type": "string"}]}, "Data", {}, {"text": "π snowman ☃"},
         "9a2d035cb54cd3d2a350e0f4dafaf79aff4d0c6fc2f27e84a50d20e7a6d4db59"),
        ({"Data": [{"name": "blob", "type": "bytes"}]}, "Data", {}, {"blob": bytes.fromhex("000102ff")},
         "83c246f04c5ef13521a133400cd0b9a906b0d9709cf2659d26b53c192421d195"),
        ({"Data": [{"name": "low", "type": "uint8"}, {"name": "high", "type": "uint256"}]},
         "Data", {}, {"low": 0, "high": 2**256 - 1},
         "2f882e31aaf7e1875f8687a23bbe866caa00326f911c844f664bf01a51def8ff"),
    ],
)
def test_ethers_615_typed_data_vectors(
    types: dict, primary: str, domain: dict, message: dict, expected: str
) -> None:
    # Fixed outputs generated with ethers@6.15.0 TypedDataEncoder.hash.
    typed = {
        "types": {"EIP712Domain": [], **types},
        "primaryType": primary,
        "domain": domain,
        "message": message,
    }
    assert eip712_hash_full_message(typed).hex() == expected


def test_agent_matches_ethers_615_reference_vector() -> None:
    domain = {
        "name": "Agent", "version": "1", "chainId": 42161,
        "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
    }
    assert eip712_hash_agent_message(domain, "wss://example.test", bytes(range(32))).hex() == (
        "b4213747a19486f4f2ceb0f7222b9733f612682fd3649afd6a605a21ac36f2da"
    )
