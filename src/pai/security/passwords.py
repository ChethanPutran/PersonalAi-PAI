"""
Deprecated compatibility shim.

The canonical implementation lives in `pai.security.auth`. This module
exists only so that older imports (`from pai.security.passwords import
hash_password`) keep working. New code should import from
`pai.security.auth` directly.
"""
from pai.security.auth import (
    ahash_password,
    averify_password,
    hash_password,
    verify_password,
)

__all__ = [
    "hash_password",
    "verify_password",
    "ahash_password",
    "averify_password",
]