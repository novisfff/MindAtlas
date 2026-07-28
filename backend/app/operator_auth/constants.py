"""Fixed operator-auth constants. Do not change without a contract version bump."""

from __future__ import annotations

OPERATOR_AUTH_CONTRACT_VERSION = "operator-auth-v1"
SESSION_COOKIE_NAME = "mindatlas_session"
CSRF_COOKIE_NAME = "mindatlas_csrf"
CSRF_HEADER_NAME = "X-MindAtlas-CSRF"
SETUP_AUTH_SCHEME = "Setup"
SESSION_IDLE_SECONDS = 12 * 60 * 60
SESSION_ABSOLUTE_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_SECONDS = 15 * 60
SESSION_HMAC_LABEL = b"mindatlas/operator-session/v1\x00"
CSRF_HMAC_LABEL = b"mindatlas/operator-csrf/v1\x00"
CONTEXT_HMAC_LABEL = b"mindatlas/operator-context/v1\x00"

# Argon2id parameters (PasswordService imports these; do not diverge).
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536
ARGON2_PARALLELISM = 2
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

PASSWORD_MIN_CODE_POINTS = 12
PASSWORD_MAX_UTF8_BYTES = 1024

SESSION_COOKIE_VERSION = "v1"
RAW_TOKEN_BYTES = 32
