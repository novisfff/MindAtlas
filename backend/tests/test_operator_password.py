import pytest

from app.operator_auth.password import PasswordPolicyError, PasswordService


def test_password_is_not_trimmed_or_normalized() -> None:
    service = PasswordService()
    secret = " １２characters! "
    encoded = service.hash(secret)
    assert service.verify(encoded, secret).valid is True
    assert service.verify(encoded, secret.strip()).valid is False
    assert service.verify(encoded, " 12characters! ").valid is False


@pytest.mark.parametrize("secret", ["short", "十一個字符abc", "a" * 1025])
def test_password_policy_rejects_wrong_bounds(secret: str) -> None:
    with pytest.raises(PasswordPolicyError):
        PasswordService().hash(secret)


def test_utf8_byte_limit_is_independent_of_code_point_minimum() -> None:
    secret = "密" * 342
    with pytest.raises(PasswordPolicyError, match="1024 UTF-8 bytes"):
        PasswordService().hash(secret)
