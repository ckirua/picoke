from importlib import import_module
from importlib.machinery import EXTENSION_SUFFIXES

from picore import crypto


_MODULE_EXPORTS = {
    "base64": ("b64encode", "b64encode_ascii", "b64decode", "b64decode_ascii"),
    "keccak": ("keccak256",),
    "ed25519": ("ed25519_public_key", "ed25519_sign", "ed25519_verify"),
    "secp256k1": ("privkey_to_pubkey", "privkey_to_address", "sign_recoverable", "recover_pubkey"),
    "eip712": ("eip712_hash_full_message", "eip712_hash_agent_message"),
    "bip137": ("bip137_signed_message_hash", "bip137_sign_message", "bip137_verify_message"),
}


def test_crypto_exports_are_native() -> None:
    assert set(crypto.__all__) == {name for names in _MODULE_EXPORTS.values() for name in names}
    for module_name, names in _MODULE_EXPORTS.items():
        module = import_module(f"picore.crypto.{module_name}")
        assert module.__file__ and module.__file__.endswith(tuple(EXTENSION_SUFFIXES))
        for name in names:
            assert getattr(crypto, name) is getattr(module, name)
