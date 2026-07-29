"""Exact Unicode password policy and Argon2id hashing."""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.operator_auth.constants import (
    ARGON2_HASH_LEN,
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_SALT_LEN,
    ARGON2_TIME_COST,
    PASSWORD_MAX_UTF8_BYTES,
    PASSWORD_MIN_CODE_POINTS,
)
from app.operator_auth.contracts import PasswordVerification


class PasswordPolicyError(ValueError):
    pass


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            hash_len=ARGON2_HASH_LEN,
            salt_len=ARGON2_SALT_LEN,
            type=Type.ID,
        )

    @staticmethod
    def validate(secret: str) -> None:
        # Exact Unicode: no strip, no normalization, no case folding.
        if len(secret) < PASSWORD_MIN_CODE_POINTS:
            raise PasswordPolicyError(
                "password must contain at least 12 Unicode code points"
            )
        if len(secret.encode("utf-8")) > PASSWORD_MAX_UTF8_BYTES:
            raise PasswordPolicyError(
                "password must not exceed 1024 UTF-8 bytes"
            )

    def hash(self, secret: str) -> str:
        self.validate(secret)
        return self._hasher.hash(secret)

    def verify(self, encoded: str, secret: str) -> PasswordVerification:
        try:
            valid = self._hasher.verify(encoded, secret)
        except (VerifyMismatchError, InvalidHashError):
            return PasswordVerification(valid=False, needs_rehash=False)
        return PasswordVerification(
            valid=bool(valid),
            needs_rehash=bool(valid and self._hasher.check_needs_rehash(encoded)),
        )
