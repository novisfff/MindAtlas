"""Operator authentication and durable browser-session lifecycle.

All idle/absolute/lockout decisions use ``repository.database_now()`` (or an
injected test clock). Raw session and CSRF tokens are never persisted — only
keyed digests. Service methods commit once per operation so rows are durable
after return (HTTP layer may still wrap in its own unit of work later).
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.operator_auth.audit import OperatorAuditRepository
from app.operator_auth.constants import (
    SESSION_ABSOLUTE_SECONDS,
    SESSION_IDLE_SECONDS,
)
from app.operator_auth.contracts import (
    IssuedSession,
    OperatorAuthAvailability,
    OperatorPrincipal,
    OperatorRole,
    RequestSecurityContext,
)
from app.operator_auth.models import OperatorAccount, OperatorSession
from app.operator_auth.password import PasswordService
from app.operator_auth.repository import NowFn, OperatorRepository
from app.operator_auth.tokens import (
    SessionMacKeyRing,
    digest_csrf,
    digest_session,
    digests_equal,
    format_csrf_cookie,
    format_session_cookie,
    issue_raw_csrf,
    issue_raw_session_cookie,
    parse_csrf_cookie,
    parse_session_cookie,
)


class AuthRejected(Exception):
    """Generic authentication failure (missing/disabled/wrong password)."""

    code = "invalid_credentials"

    def __str__(self) -> str:  # noqa: D105
        return self.code


class LoginLocked(Exception):
    """Login is locked under database-time lockout."""

    code = "login_locked"

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(0, int(retry_after_seconds))
        super().__init__(self.code)

    def __str__(self) -> str:  # noqa: D105
        return self.code


class CsrfRejected(Exception):
    """CSRF cookie/header pair failed validation."""

    code = "csrf_rejected"

    def __str__(self) -> str:  # noqa: D105
        return self.code


class SessionRejected(Exception):
    """Session could not be used for a privileged mutation (already invalid)."""

    code = "invalid_session"

    def __str__(self) -> str:  # noqa: D105
        return self.code


@dataclass(frozen=True)
class SessionResolution:
    """Result of a successful ``resolve_session`` call.

    ``rotated_cookie`` is ``(session_cookie_value, csrf_cookie_value)`` when the
    row was re-MACed under the active key (previous-key success path). Absolute
    expiry is never extended.
    """

    principal: OperatorPrincipal
    idle_expires_at: datetime
    absolute_expires_at: datetime
    rotated_cookie: tuple[str, str] | None = None
    hmac_key_id: str = ""


class OperatorAuthService:
    """Password authentication, session issue/resolve, CSRF, and revocation."""

    def __init__(
        self,
        db: Session,
        *,
        key_ring: SessionMacKeyRing | None,
        repository: OperatorRepository | None = None,
        password_service: PasswordService | None = None,
        audit: OperatorAuditRepository | None = None,
        now_fn: NowFn | Callable[[], datetime] | None = None,
        auto_commit: bool = True,
    ) -> None:
        self.db = db
        self.key_ring = key_ring
        self.repository = repository or OperatorRepository(db, now_fn=now_fn)
        self.password_service = password_service or PasswordService()
        self.audit = audit or OperatorAuditRepository(
            db, operator_repository=self.repository
        )
        self._auto_commit = auto_commit

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _commit(self) -> None:
        if self._auto_commit:
            self.db.commit()

    def _require_key_ring(self) -> SessionMacKeyRing:
        if self.key_ring is None:
            raise RuntimeError("operator_auth_unavailable")
        return self.key_ring

    def _principal_for(
        self, account: OperatorAccount, session_id: UUID
    ) -> OperatorPrincipal:
        role: OperatorRole = account.role  # type: ignore[assignment]
        return OperatorPrincipal(
            operator_id=account.id,
            role=role,
            session_id=session_id,
        )

    def _normalize_dt(self, value: datetime, *, now: datetime) -> datetime:
        if value.tzinfo is None and now.tzinfo is not None:
            return value.replace(tzinfo=now.tzinfo)
        return value

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def availability(self) -> OperatorAuthAvailability:
        """Report whether auth can serve login / sessions without leaking secrets."""
        reasons: list[str] = []
        if self.key_ring is None or not self.key_ring.keys:
            reasons.append("operator_auth_unavailable")
        account = self.repository.get_singleton_account(for_update=False)
        if account is None:
            reasons.append("initialization_required")
        # Available when the key ring is present; init-required is informational.
        available = "operator_auth_unavailable" not in reasons
        return OperatorAuthAvailability(
            available=available, reason_codes=tuple(reasons)
        )

    # ------------------------------------------------------------------
    # Authentication + session issue
    # ------------------------------------------------------------------

    def authenticate_password(
        self,
        password: str,
        context: RequestSecurityContext,
        *,
        commit: bool | None = None,
    ) -> OperatorAccount:
        """Verify the singleton operator password under row lock.

        Raises ``LoginLocked`` or generic ``AuthRejected``. On success the
        failure window is cleared and optional Argon2 rehash is applied. Does
        not issue a session — call ``issue_session`` or ``login``.

        Failure paths always commit (lockout + audit must be durable). Success
        honours ``commit`` / ``auto_commit`` so ``login`` can issue a session in
        the same transaction.
        """
        should_commit_success = self._auto_commit if commit is None else commit

        account = self.repository.get_singleton_account(for_update=True)
        if account is None or not account.enabled:
            self.audit.append(
                event_type="login_rejected",
                outcome="rejected",
                context=context,
                operator_id=account.id if account is not None else None,
                session_id=None,
                reason_code="invalid_credentials",
            )
            self.db.commit()
            raise AuthRejected()

        if self.repository.is_login_locked(account):
            retry = self.repository.lockout_retry_after_seconds(account)
            self.audit.append(
                event_type="login_locked",
                outcome="rejected",
                context=context,
                operator_id=account.id,
                session_id=None,
                reason_code="login_locked",
                metadata={"retry_after_seconds": retry},
            )
            self.db.commit()
            raise LoginLocked(retry)

        verification = self.password_service.verify(account.password_hash, password)
        if not verification.valid:
            self.repository.record_login_failure(account.id)
            # Re-load for lock state after failure recording.
            account = self.repository.get_account_by_id(account.id, for_update=True)
            assert account is not None
            if self.repository.is_login_locked(account):
                retry = self.repository.lockout_retry_after_seconds(account)
                self.audit.append(
                    event_type="login_locked",
                    outcome="rejected",
                    context=context,
                    operator_id=account.id,
                    session_id=None,
                    reason_code="login_locked",
                    metadata={"retry_after_seconds": retry},
                )
                self.db.commit()
                raise LoginLocked(retry)
            self.audit.append(
                event_type="login_rejected",
                outcome="rejected",
                context=context,
                operator_id=account.id,
                session_id=None,
                reason_code="invalid_credentials",
            )
            self.db.commit()
            raise AuthRejected()

        self.repository.clear_login_failures(account.id)
        account = self.repository.get_account_by_id(account.id, for_update=True)
        assert account is not None

        if verification.needs_rehash:
            account.password_hash = self.password_service.hash(password)
            account.updated_at = self.repository.database_now()
            self.db.flush()

        if should_commit_success:
            self.db.commit()
        return account

    def issue_session(
        self,
        account: OperatorAccount,
        context: RequestSecurityContext,
        *,
        commit: bool | None = None,
    ) -> IssuedSession:
        """Create a durable session row under the active MAC key and audit it."""
        key_ring = self._require_key_ring()
        now = self.repository.database_now()
        session_id = uuid.uuid4()
        cookie_value, raw_session = issue_raw_session_cookie(session_id)
        csrf_value, raw_csrf = issue_raw_csrf()
        absolute = now + timedelta(seconds=SESSION_ABSOLUTE_SECONDS)
        idle = min(now + timedelta(seconds=SESSION_IDLE_SECONDS), absolute)
        row = OperatorSession(
            id=session_id,
            operator_account_id=account.id,
            token_digest=digest_session(
                key=key_ring.active_key, session_id=session_id, raw=raw_session
            ),
            csrf_digest=digest_csrf(
                key=key_ring.active_key, session_id=session_id, raw=raw_csrf
            ),
            hmac_key_id=key_ring.active_key_id,
            password_revision=int(account.password_revision),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle,
            absolute_expires_at=absolute,
            request_digest=context.request_digest,
            user_agent_digest=context.user_agent_digest,
            network_digest=context.network_digest,
        )
        self.repository.add_session(row)
        principal = self._principal_for(account, session_id)
        self.audit.append(
            event_type="session_created",
            outcome="succeeded",
            context=context,
            operator_id=account.id,
            session_id=session_id,
        )
        if commit if commit is not None else self._auto_commit:
            self.db.commit()
        return IssuedSession(
            principal=principal,
            session_cookie_value=cookie_value,
            csrf_cookie_value=csrf_value,
            idle_expires_at=idle,
            absolute_expires_at=absolute,
        )

    def login(self, password: str, context: RequestSecurityContext) -> IssuedSession:
        """Authenticate then issue a session in one committed transaction."""
        # Hold commit until both auth + issue succeed so lockout clears and the
        # new session row land together. login_succeeded is audit-only for the
        # password path; issue_session alone (e.g. post-init) does not emit it.
        account = self.authenticate_password(password, context, commit=False)
        issued = self.issue_session(account, context, commit=False)
        self.audit.append(
            event_type="login_succeeded",
            outcome="succeeded",
            context=context,
            operator_id=account.id,
            session_id=issued.principal.session_id,
        )
        self.db.commit()
        return issued

    # ------------------------------------------------------------------
    # Resolve / touch / rotate
    # ------------------------------------------------------------------

    def resolve_session(
        self,
        session_cookie_value: str | None,
        context: RequestSecurityContext,
        *,
        csrf_cookie_value: str | None = None,
    ) -> SessionResolution | None:
        """Validate a browser session cookie and touch idle expiry.

        Returns ``None`` for any failure mode (malformed, revoked, expired,
        unknown key, disabled account, revision mismatch). Expiry and policy
        failures are revoked durably when a row can be identified.

        When the row is under a previous key **and** both session and CSRF raws
        are supplied and valid under that previous key, digests are re-MACed
        under the active key using the **same** raw bytes (no new entropy) and
        ``rotated_cookie`` re-encodes those raws. Absolute expiry is unchanged.
        Without a valid CSRF raw, the session still resolves under the previous
        key but is not rotated on this request.
        """
        if not session_cookie_value or not isinstance(session_cookie_value, str):
            return None
        try:
            session_id, raw_session = parse_session_cookie(session_cookie_value)
        except ValueError:
            return None

        key_ring = self.key_ring
        if key_ring is None:
            return None

        row = self.repository.get_session_by_id(session_id, for_update=True)
        if row is None:
            return None
        if row.revoked_at is not None:
            return None

        now = self.repository.database_now()
        idle_expires = self._normalize_dt(row.idle_expires_at, now=now)
        absolute_expires = self._normalize_dt(row.absolute_expires_at, now=now)

        # Absolute first: once absolute is reached, that reason wins even if idle
        # would also have expired (idle is always <= absolute).
        if now >= absolute_expires:
            self._expire_session(row, reason="absolute_expired", context=context, now=now)
            return None
        if now >= idle_expires:
            self._expire_session(row, reason="idle_expired", context=context, now=now)
            return None

        key = key_ring.get(row.hmac_key_id)
        if key is None:
            self.repository.revoke_session(
                row, reason="hmac_key_removed", revoked_at=now
            )
            self.audit.append(
                event_type="session_key_revoked",
                outcome="succeeded",
                context=context,
                operator_id=row.operator_account_id,
                session_id=row.id,
                reason_code="hmac_key_removed",
            )
            self._commit()
            return None

        expected = digest_session(key=key, session_id=row.id, raw=raw_session)
        if not digests_equal(expected, row.token_digest):
            # Token mismatch: do not revoke (could be a probe); just reject.
            return None

        account = self.repository.get_account_by_id(row.operator_account_id)
        if account is None or not account.enabled:
            self.repository.revoke_session(
                row, reason="account_disabled", revoked_at=now
            )
            self.audit.append(
                event_type="session_revoked",
                outcome="succeeded",
                context=context,
                operator_id=row.operator_account_id,
                session_id=row.id,
                reason_code="account_disabled",
            )
            self._commit()
            return None

        if int(row.password_revision) != int(account.password_revision):
            self.repository.revoke_session(
                row, reason="password_revision_mismatch", revoked_at=now
            )
            self.audit.append(
                event_type="session_revoked",
                outcome="succeeded",
                context=context,
                operator_id=account.id,
                session_id=row.id,
                reason_code="password_revision_mismatch",
            )
            self._commit()
            return None

        # Successful touch — never extend absolute expiry. Creation request-
        # context digests are immutable audit/binding fields and are not
        # overwritten on touch.
        row.last_seen_at = now
        row.idle_expires_at = min(
            now + timedelta(seconds=SESSION_IDLE_SECONDS),
            absolute_expires,
        )

        rotated: tuple[str, str] | None = None
        if row.hmac_key_id != key_ring.active_key_id:
            # Previous-key success: only rotate when both raws are present and
            # the CSRF raw validates under the previous key. Re-MAC the same
            # raws under the active key — do not mint new entropy.
            raw_csrf: bytes | None = None
            if csrf_cookie_value and isinstance(csrf_cookie_value, str):
                try:
                    candidate = parse_csrf_cookie(csrf_cookie_value)
                except ValueError:
                    candidate = None
                else:
                    expected_csrf = digest_csrf(
                        key=key, session_id=row.id, raw=candidate
                    )
                    if digests_equal(expected_csrf, row.csrf_digest):
                        raw_csrf = candidate

            if raw_csrf is not None:
                row.token_digest = digest_session(
                    key=key_ring.active_key, session_id=row.id, raw=raw_session
                )
                row.csrf_digest = digest_csrf(
                    key=key_ring.active_key, session_id=row.id, raw=raw_csrf
                )
                row.hmac_key_id = key_ring.active_key_id
                rotated = (
                    format_session_cookie(row.id, raw_session),
                    format_csrf_cookie(raw_csrf),
                )

        self.db.flush()
        self._commit()

        principal = self._principal_for(account, row.id)
        return SessionResolution(
            principal=principal,
            idle_expires_at=self._normalize_dt(row.idle_expires_at, now=now),
            absolute_expires_at=self._normalize_dt(row.absolute_expires_at, now=now),
            rotated_cookie=rotated,
            hmac_key_id=row.hmac_key_id,
        )

    def _expire_session(
        self,
        row: OperatorSession,
        *,
        reason: str,
        context: RequestSecurityContext,
        now: datetime,
    ) -> None:
        self.repository.revoke_session(row, reason=reason, revoked_at=now)
        self.audit.append(
            event_type="session_expired",
            outcome="succeeded",
            context=context,
            operator_id=row.operator_account_id,
            session_id=row.id,
            reason_code=reason,
        )
        self._commit()

    # ------------------------------------------------------------------
    # CSRF
    # ------------------------------------------------------------------

    def verify_csrf(
        self,
        *,
        resolution: SessionResolution,
        csrf_cookie_value: str | None,
        csrf_header_value: str | None,
    ) -> None:
        """Validate double-submit CSRF cookie + header against the session row.

        Raises ``CsrfRejected`` on any mismatch or malformed value.
        """
        if not csrf_cookie_value or not csrf_header_value:
            raise CsrfRejected()
        if not isinstance(csrf_cookie_value, str) or not isinstance(
            csrf_header_value, str
        ):
            raise CsrfRejected()
        if not hmac.compare_digest(csrf_cookie_value, csrf_header_value):
            raise CsrfRejected()

        key_ring = self._require_key_ring()
        row = self.repository.get_session_by_id(resolution.principal.session_id)
        if row is None or row.revoked_at is not None:
            raise CsrfRejected()

        key = key_ring.get(row.hmac_key_id)
        if key is None:
            raise CsrfRejected()

        try:
            raw = parse_csrf_cookie(csrf_cookie_value)
        except ValueError as exc:
            raise CsrfRejected() from exc

        expected = digest_csrf(key=key, session_id=row.id, raw=raw)
        if not digests_equal(expected, row.csrf_digest):
            raise CsrfRejected()

    # ------------------------------------------------------------------
    # Password change + revocation
    # ------------------------------------------------------------------

    def change_password(
        self,
        *,
        principal: OperatorPrincipal,
        current_password: str,
        new_password: str,
        context: RequestSecurityContext,
    ) -> None:
        """Verify current password, rehash, bump revision, revoke all sessions."""
        account = self.repository.get_account_by_id(
            principal.operator_id, for_update=True
        )
        if account is None or not account.enabled:
            raise AuthRejected()

        verification = self.password_service.verify(
            account.password_hash, current_password
        )
        if not verification.valid:
            self.audit.append(
                event_type="login_rejected",
                outcome="rejected",
                context=context,
                operator_id=account.id,
                session_id=principal.session_id,
                reason_code="invalid_credentials",
            )
            self._commit()
            raise AuthRejected()

        new_hash = self.password_service.hash(new_password)

        now = self.repository.database_now()
        account.password_hash = new_hash
        account.password_revision = int(account.password_revision) + 1
        account.password_changed_at = now
        account.updated_at = now
        self.db.flush()

        revoked = self.repository.revoke_all_sessions_for_account(
            account.id, reason="password_changed", revoked_at=now
        )
        self.audit.append(
            event_type="password_changed",
            outcome="succeeded",
            context=context,
            operator_id=account.id,
            session_id=principal.session_id,
            metadata={"revoked_sessions": int(revoked)},
        )
        self._commit()

    def revoke_current(
        self,
        *,
        principal: OperatorPrincipal,
        context: RequestSecurityContext,
    ) -> None:
        """Logout: revoke the caller's current session durably."""
        row = self.repository.get_session_by_id(
            principal.session_id, for_update=True
        )
        now = self.repository.database_now()
        if row is not None and row.revoked_at is None:
            self.repository.revoke_session(row, reason="logout", revoked_at=now)
        self.audit.append(
            event_type="logout",
            outcome="succeeded",
            context=context,
            operator_id=principal.operator_id,
            session_id=principal.session_id,
        )
        self._commit()

    def revoke_all(
        self,
        *,
        principal: OperatorPrincipal,
        context: RequestSecurityContext,
        reason: str = "revoke_all",
    ) -> int:
        """Revoke every active session for the principal's account."""
        # Bound reason to allowlist; external free-text belongs in audit metadata.
        revoke_reason = reason if reason in {
            "revoke_all",
            "password_changed",
            "maintenance",
            "logout",
        } else "revoke_all"
        now = self.repository.database_now()
        count = self.repository.revoke_all_sessions_for_account(
            principal.operator_id, reason=revoke_reason, revoked_at=now
        )
        self.audit.append(
            event_type="revoke_all",
            outcome="succeeded",
            context=context,
            operator_id=principal.operator_id,
            session_id=principal.session_id,
            reason_code=revoke_reason,
            metadata={"revoked_sessions": int(count)},
        )
        self._commit()
        return count

    def revoke_unverifiable_sessions(
        self,
        *,
        context: RequestSecurityContext,
    ) -> int:
        """Durably revoke active sessions whose hmac_key_id is not in the ring."""
        key_ring = self._require_key_ring()
        known = set(key_ring.keys.keys())
        now = self.repository.database_now()
        rows = self.repository.list_active_sessions(for_update=True)
        count = 0
        for row in rows:
            if row.hmac_key_id in known:
                continue
            self.repository.revoke_session(
                row, reason="hmac_key_removed", revoked_at=now
            )
            self.audit.append(
                event_type="session_key_revoked",
                outcome="succeeded",
                context=context,
                operator_id=row.operator_account_id,
                session_id=row.id,
                reason_code="hmac_key_removed",
            )
            count += 1
        if count:
            self._commit()
        return count
