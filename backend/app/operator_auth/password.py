"""Exact Unicode password policy and Argon2id hashing."""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.operator_auth.contracts import PasswordVerification


class PasswordPolicyError(ValueError):
    pass


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    @staticmethod
    def validate(secret: str) -> None:
        # Exact Unicode: no strip, no normalization, no case folding.
        if len(secret) < 12:
            raise PasswordPolicyError(
                "password must contain at least 12 Unicode code points"
            )
        if len(secret.encode("utf-8")) > 1024:
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
