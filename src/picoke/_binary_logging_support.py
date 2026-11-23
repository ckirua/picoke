"""Python recursion frames and deep fallbacks for binary field validation."""

import math
from collections.abc import Mapping

from .logging import _json_fields as fallback_materialize


def fallback_numeric(value):
    """Match the numeric pass when native traversal reaches its depth bound."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 64):
            raise OverflowError("integer outside MessagePack 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    if isinstance(value, dict):
        for item in value.values():
            fallback_numeric(item)
        return value
    if isinstance(value, list):
        for item in value:
            fallback_numeric(item)
        return value
    raise TypeError(f"unsupported field value type: {type(value).__name__}")


def fallback_value(value):
    """Preserve Python isinstance semantics for non-exact built-in types."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 64):
            raise OverflowError("integer outside MessagePack 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    if isinstance(value, Mapping):
        return fallback_numeric(fallback_materialize(value, set()))
    if isinstance(value, list):
        return fallback_numeric(fallback_materialize(value, set()))
    raise TypeError(f"unsupported field value type: {type(value).__name__}")


# These Python calls deliberately retain interpreter recursion-limit accounting.
def call2(function, value, active):
    return function(value, active)


def call1(function, value):
    return function(value)


# Entry frames account for the original Python validator and traversal frames.
def enter2(function, value, active):
    return call2(function, value, active)


def enter1(function, value):
    return call1(function, value)
