"""Ethereum-compatible EIP-712 typed-data hashing."""

import re

from cpython.object cimport PyObject_IsInstance
from cpython.bytes cimport PyBytes_AS_STRING, PyBytes_Check, PyBytes_GET_SIZE, PyBytes_FromStringAndSize
from cpython.long cimport PyLong_AsNativeBytes
from cpython.unicode cimport PyUnicode_AsUTF8String, PyUnicode_READ_CHAR
from libc.string cimport memcpy, memset

cdef extern from "Python.h":
    int Py_GetRecursionLimit()
    int Py_EnterRecursiveCall(const char *where) except *
    void Py_LeaveRecursiveCall()
    int Py_ASNATIVEBYTES_BIG_ENDIAN
    int Py_ASNATIVEBYTES_UNSIGNED_BUFFER

from .keccak import keccak256

cdef bytes _AGENT_DOMAIN_TYPE_HASH = keccak256(
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
cdef bytes _AGENT_TYPE_HASH = keccak256(b"Agent(string source,bytes32 connectionId)")

_ARRAY_SUFFIX = re.compile(r"\[(\d*)\]$")
_DOMAIN_FIELDS = {
    "name": "string",
    "version": "string",
    "chainId": "uint256",
    "verifyingContract": "address",
    "salt": "bytes32",
}

_PRIMITIVE_TYPE = re.compile(r"^(?:string|bytes|bool|address|u?int(?:[0-9]+)?|bytes[0-9]+)$")


def _base_type(type_name: str) -> str:
    while match := _ARRAY_SUFFIX.search(type_name):
        type_name = type_name[: match.start()]
    return type_name


def _type_dependencies(type_name: str, types: dict) -> set[str]:
    cdef list pending = [_base_type(type_name)]
    found = set()
    while pending:
        current = pending.pop()
        if current in found or _PRIMITIVE_TYPE.fullmatch(current):
            continue
        if current not in types:
            raise ValueError(f"Type {current!r} is not defined")
        found.add(current)
        for field in types[current]:
            pending.append(_base_type(field["type"]))
    return found


cdef object _validate_value(
    object type_name, object value, object types, object field_name, int remaining
):
    cdef object match
    cdef object normalized
    cdef object item
    cdef object key
    cdef object field
    cdef object child
    cdef object replacement
    cdef dict replacement_dict
    cdef Py_ssize_t index
    cdef bint changed
    # Native frames do not count against the Python recursion limit.
    if remaining <= 0:
        raise RecursionError("maximum recursion depth exceeded while validating EIP-712 data")
    Py_EnterRecursiveCall(" while validating EIP-712 data")
    try:
        if value is None:
            raise ValueError(f"Missing value for field {field_name!r}")
        match = _ARRAY_SUFFIX.search(type_name)
        if match:
            if not PyObject_IsInstance(value, (list, tuple)):
                raise TypeError(f"Field {field_name!r} must be an array")
            length = match.group(1)
            if length and len(value) != int(length):
                raise ValueError(f"Field {field_name!r} must contain {length} items")
            element_type = type_name[: match.start()]
            replacement = [] if isinstance(value, tuple) else None
            changed = isinstance(value, tuple)
            for index, item in enumerate(value):
                normalized = _validate_value(element_type, item, types, field_name, remaining - 1)
                if normalized is not item and replacement is None:
                    replacement = list(value[:index])
                    changed = True
                if replacement is not None:
                    replacement.append(normalized)
            return replacement if changed else value
        if type_name in types:
            if not PyObject_IsInstance(value, dict):
                raise TypeError(f"Field {field_name!r} must be a struct")
            replacement_dict = None
            for field in types[type_name]:
                key = field["name"]
                if key not in value:
                    raise ValueError(f"Missing value for field {key!r}")
                item = value[key]
                child = _validate_value(field["type"], item, types, key, remaining - 1)
                if child is not item:
                    if replacement_dict is None:
                        replacement_dict = dict(value)
                    replacement_dict[key] = child
            return value if replacement_dict is None else replacement_dict
        return value
    finally:
        Py_LeaveRecursiveCall()


def eip712_hash_full_message(full_message: dict) -> bytes:
    """Return the 32-byte EIP-712 digest for a full typed-data envelope."""
    if not PyObject_IsInstance(full_message, dict):
        raise TypeError("full_message must be a dict")
    try:
        types = full_message["types"]
        primary_type = full_message["primaryType"]
        domain = full_message["domain"]
        message = full_message["message"]
    except KeyError:
        raise
    if not PyObject_IsInstance(types, dict) or not PyObject_IsInstance(primary_type, str):
        raise TypeError("types must be a dict and primaryType must be a str")
    if not PyObject_IsInstance(domain, dict) or not PyObject_IsInstance(message, dict):
        raise TypeError("domain and message must be dicts")
    domain_fields = types.get("EIP712Domain", [])
    order = {name: index for index, name in enumerate(_DOMAIN_FIELDS)}
    previous = -1
    seen = set()
    for field in domain_fields:
        name = field["name"]
        if name not in _DOMAIN_FIELDS or field["type"] != _DOMAIN_FIELDS[name]:
            raise ValueError(f"Invalid EIP712Domain field {name!r}")
        if name in seen or order[name] <= previous:
            raise ValueError("EIP712Domain fields must be unique and in canonical order")
        seen.add(name)
        previous = order[name]
    normalized_domain = domain
    for field in domain_fields:
        name = field["name"]
        if name not in domain:
            raise ValueError(f"Missing value for domain field {name!r}")
        value = domain[name]
        normalized = _validate_value(field["type"], value, types, name, Py_GetRecursionLimit())
        if normalized is not value:
            if normalized_domain is domain:
                normalized_domain = dict(domain)
            normalized_domain[name] = normalized

    selected_types = _type_dependencies(primary_type, types)
    selected_types.add("EIP712Domain")
    if primary_type not in types:
        raise ValueError(f"Type {primary_type!r} is not defined")
    normalized_message = _validate_value(primary_type, message, types, primary_type, Py_GetRecursionLimit())
    selected = {name: types[name] for name in selected_types if name in types}
    envelope = full_message if normalized_domain is domain and normalized_message is message else {
        **full_message, "domain": normalized_domain, "message": normalized_message
    }
    return _hash_provider_message({**envelope, "types": selected})


cdef bytes _hash_provider_message(dict envelope):
    # Canonical Agent hashing uses only local Keccak. Import the generic
    # provider after validation so unrelated crypto calls avoid its startup.
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak

    signable = encode_typed_data(full_message=envelope)
    return keccak(b"\x19" + signable.version + signable.header + signable.body)


cdef inline int _hex_nibble(Py_UCS4 digit) noexcept:
    if 48 <= digit <= 57:  # 0–9
        return <int>digit - 48
    if 65 <= digit <= 70:  # A–F
        return <int>digit - 55
    if 97 <= digit <= 102:  # a–f
        return <int>digit - 87
    return -1


cdef inline void _copy_digest(char *dst, bytes digest) except *:
    if digest is None or len(digest) != 32:
        raise ValueError("Keccak-256 must return 32 bytes")
    memcpy(dst, PyBytes_AS_STRING(digest), 32)


cdef object _try_hash_agent_fixed(object domain, object source, object connection_id):
    cdef object name, version, chain_id, address
    cdef bytes name_bytes, version_bytes, source_bytes
    cdef bytes domain_data, message_data, preimage
    cdef char[20] address_bytes
    cdef char *dst
    cdef int index, high, low

    if type(domain) is not dict or type(source) is not str or type(connection_id) is not bytes:
        return None
    if len(domain) != 4:
        return None
    # Non-exact keys can run user-defined equality during lookup.
    for key in domain:
        if type(key) is not str:
            return None
    name = domain.get("name")
    version = domain.get("version")
    chain_id = domain.get("chainId")
    address = domain.get("verifyingContract")
    if type(name) is not str or type(version) is not str or type(address) is not str:
        return None
    if type(chain_id) is not int or chain_id < 0 or chain_id >= (1 << 256):
        return None
    if len(address) != 42 or PyUnicode_READ_CHAR(address, 0) != 48 or PyUnicode_READ_CHAR(address, 1) != 120:
        return None
    for index in range(20):
        high = _hex_nibble(PyUnicode_READ_CHAR(address, 2 + 2 * index))
        low = _hex_nibble(PyUnicode_READ_CHAR(address, 3 + 2 * index))
        if high < 0 or low < 0:
            return None
        address_bytes[index] = <char>((high << 4) | low)
    try:
        name_bytes = PyUnicode_AsUTF8String(name)
        version_bytes = PyUnicode_AsUTF8String(version)
        source_bytes = PyUnicode_AsUTF8String(source)
    except UnicodeEncodeError:
        return None

    # Fill each newly allocated bytes object before exposing it to the hasher.
    # EIP-712 encodeData starts with the type hash, then one ABI word per field.
    domain_data = PyBytes_FromStringAndSize(NULL, 160)
    dst = PyBytes_AS_STRING(domain_data)
    _copy_digest(dst, _AGENT_DOMAIN_TYPE_HASH)
    _copy_digest(dst + 32, keccak256(name_bytes))
    _copy_digest(dst + 64, keccak256(version_bytes))
    PyLong_AsNativeBytes(chain_id, dst + 96, 32,
                         Py_ASNATIVEBYTES_BIG_ENDIAN | Py_ASNATIVEBYTES_UNSIGNED_BUFFER)
    memset(dst + 128, 0, 12)
    memcpy(dst + 140, address_bytes, 20)

    message_data = PyBytes_FromStringAndSize(NULL, 96)
    dst = PyBytes_AS_STRING(message_data)
    _copy_digest(dst, _AGENT_TYPE_HASH)
    _copy_digest(dst + 32, keccak256(source_bytes))
    memcpy(dst + 64, PyBytes_AS_STRING(connection_id), 32)

    preimage = PyBytes_FromStringAndSize(NULL, 66)
    dst = PyBytes_AS_STRING(preimage)
    dst[0], dst[1] = 0x19, 0x01
    _copy_digest(dst + 2, keccak256(domain_data))
    _copy_digest(dst + 34, keccak256(message_data))
    return keccak256(preimage)


def eip712_hash_agent_message(domain: dict, source: str, connection_id: bytes) -> bytes:
    """Hash the legacy Agent schema, requiring a complete bytes32 identifier."""
    if not PyObject_IsInstance(domain, dict):
        raise TypeError("domain must be a dict")
    if not PyObject_IsInstance(source, str):
        raise TypeError("source must be a str")
    if not PyObject_IsInstance(connection_id, bytes):
        raise TypeError("connection_id must be bytes")
    if len(connection_id) != 32:
        raise ValueError("connection_id must be exactly 32 bytes")
    optimized = _try_hash_agent_fixed(domain, source, connection_id)
    if optimized is not None:
        return optimized
    # A bytes subclass can override len(), but the fallback encodes its storage.
    if PyBytes_Check(connection_id) and PyBytes_GET_SIZE(connection_id) != 32:
        raise ValueError("connection_id must be exactly 32 bytes")
    return eip712_hash_full_message(
        {
            "types": {"EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ], "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ]},
            "primaryType": "Agent",
            "domain": domain,
            "message": {"source": source, "connectionId": connection_id},
        }
    )

