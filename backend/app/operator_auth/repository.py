"""Operator account / session repository with database-time lockout.

All lockout and expiry decisions use ``database_now()`` (or an injected
``now_fn`` for unit tests). Application wall clock is never consulted.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Iterable
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.operator_auth.constants import (
    LOGIN_FAILURE_LIMIT,
    LOGIN_LOCK_SECONDS,
    LOGIN_WINDOW_SECONDS,
)
from app.operator_auth.models import (
    OPERATOR_SESSION_REVOKE_REASONS,
    OperatorAccount,
    OperatorSession,
)
from app.operator_auth.password import PasswordService
from app.system_settings.models import AppSetting

# Prefer a local constant to avoid circular imports with initialization services.
SYSTEM_INITIALIZATION_STATE_KEY = "system_initialization_state"

# PostgreSQL transaction-scoped advisory lock key: ASCII "MAOP".
INITIALIZATION_LOCK_KEY = 0x4D_41_4F_50

NowFn = Callable[[], datetime]


class OperatorRepository:
    """Persistence helpers for the singleton operator account and sessions."""

    def __init__(
        self,
        db: Session,
        *,
        now_fn: NowFn | None = None,
        password_service: PasswordService | None = None,
    ) -> None:
        self.db = db
        self._now_fn = now_fn
        self._password_service = password_service or PasswordService()

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------

    def database_now(self) -> datetime:
        """Return the current database clock (or injected test clock)."""
        if self._now_fn is not None:
            return self._now_fn()
        return self.db.execute(select(func.now())).scalar_one()

    # ------------------------------------------------------------------
    # Initialization locking
    # ------------------------------------------------------------------

    def lock_initialization(self) -> None:
        """Acquire the singleton initialization lock inside the outer transaction.

        On PostgreSQL uses a transaction advisory lock plus row locks on the
        initialization marker and operator singleton. Callers must recheck both
        the marker and the singleton account after this returns.
        """
        bind = self.db.get_bind() if hasattr(self.db, "get_bind") else self.db.bind
        dialect_name = bind.dialect.name if bind is not None else None
        if dialect_name == "postgresql":
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": INITIALIZATION_LOCK_KEY},
            )
        self.db.execute(
            select(AppSetting)
            .where(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
            .with_for_update()
        ).scalar_one_or_none()
        self.db.execute(
            select(OperatorAccount)
            .where(OperatorAccount.singleton_key == "operator")
            .with_for_update()
        ).scalar_one_or_none()

    def assert_uninitialized(self) -> None:
        """Raise if an enabled operator account already exists.

        The initialization coordinator calls this after ``lock_initialization``.
        """
        account = self.get_singleton_account(for_update=False)
        if account is not None:
            raise RuntimeError("system_already_initialized")

    # ------------------------------------------------------------------
    # Account helpers
    # ------------------------------------------------------------------

    def get_singleton_account(
        self, *, for_update: bool = False
    ) -> OperatorAccount | None:
        stmt = select(OperatorAccount).where(
            OperatorAccount.singleton_key == "operator"
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_account_by_id(
        self, account_id: UUID, *, for_update: bool = False
    ) -> OperatorAccount | None:
        stmt = select(OperatorAccount).where(OperatorAccount.id == account_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def seed_account(
        self,
        *,
        password: str,
        role: str = "operator",
        enabled: bool = True,
    ) -> OperatorAccount:
        """Create the singleton operator account (tests + initialization staging)."""
        now = self.database_now()
        account = OperatorAccount(
            singleton_key="operator",
            role=role,
            password_hash=self._password_service.hash(password),
            password_revision=1,
            enabled=enabled,
            failed_login_window_started_at=None,
            failed_login_count=0,
            locked_until=None,
            password_changed_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(account)
        self.db.flush()
        return account

    def record_login_failure(self, account_id: UUID) -> OperatorAccount:
        """Record a failed login under row lock using database time.

        Five failures inside ``LOGIN_WINDOW_SECONDS`` set ``locked_until`` to
        ``now + LOGIN_LOCK_SECONDS``. Outside the window a new window starts.
        """
        account = self.db.execute(
            select(OperatorAccount)
            .where(OperatorAccount.id == account_id)
            .with_for_update()
        ).scalar_one()
        now = self.database_now()
        window_started = account.failed_login_window_started_at
        if window_started is not None and window_started.tzinfo is None and now.tzinfo is not None:
            # Normalize naive SQLite timestamps against aware frozen clocks.
            window_started = window_started.replace(tzinfo=now.tzinfo)
        if window_started is None or (
            now - window_started
        ).total_seconds() >= LOGIN_WINDOW_SECONDS:
            # Restart the failure window only. Do not clear a still-active lock:
            # locked_until can outlive the window when failures are spaced, and
            # expiry is owned by is_login_locked / clear_login_failures.
            account.failed_login_window_started_at = now
            account.failed_login_count = 1
        else:
            account.failed_login_count = int(account.failed_login_count or 0) + 1
        if int(account.failed_login_count) >= LOGIN_FAILURE_LIMIT:
            account.locked_until = now + timedelta(seconds=LOGIN_LOCK_SECONDS)
        account.updated_at = now
        self.db.flush()
        return account

    def clear_login_failures(self, account_id: UUID) -> OperatorAccount:
        """Clear failure count, window, and lock after successful authentication."""
        account = self.db.execute(
            select(OperatorAccount)
            .where(OperatorAccount.id == account_id)
            .with_for_update()
        ).scalar_one()
        now = self.database_now()
        account.failed_login_count = 0
        account.failed_login_window_started_at = None
        account.locked_until = None
        account.updated_at = now
        self.db.flush()
        return account

    def is_login_locked(self, account: OperatorAccount) -> bool:
        """True when ``locked_until`` is still in the future under database time."""
        if account.locked_until is None:
            return False
        now = self.database_now()
        locked_until = account.locked_until
        if locked_until.tzinfo is None and now.tzinfo is not None:
            locked_until = locked_until.replace(tzinfo=now.tzinfo)
        return locked_until > now

    def lockout_retry_after_seconds(self, account: OperatorAccount) -> int:
        """Bounded non-negative seconds remaining on the active lock, else 0."""
        if account.locked_until is None:
            return 0
        now = self.database_now()
        locked_until = account.locked_until
        if locked_until.tzinfo is None and now.tzinfo is not None:
            locked_until = locked_until.replace(tzinfo=now.tzinfo)
        remaining = (locked_until - now).total_seconds()
        if remaining <= 0:
            return 0
        return int(remaining)

    # ------------------------------------------------------------------
    # Session helpers (used by Task 4 service)
    # ------------------------------------------------------------------

    def add_session(self, session_row: OperatorSession) -> OperatorSession:
        self.db.add(session_row)
        self.db.flush()
        return session_row

    def get_session_by_id(
        self, session_id: UUID, *, for_update: bool = False
    ) -> OperatorSession | None:
        stmt = select(OperatorSession).where(OperatorSession.id == session_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_session_by_token_digest(
        self, token_digest: str, *, for_update: bool = False
    ) -> OperatorSession | None:
        stmt = select(OperatorSession).where(
            OperatorSession.token_digest == token_digest
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def revoke_session(
        self,
        session_row: OperatorSession,
        *,
        reason: str,
        revoked_at: datetime | None = None,
    ) -> OperatorSession:
        if reason not in OPERATOR_SESSION_REVOKE_REASONS:
            raise ValueError(f"unsupported revoke reason: {reason!r}")
        if session_row.revoked_at is not None:
            return session_row
        session_row.revoked_at = revoked_at or self.database_now()
        session_row.revoke_reason = reason
        self.db.flush()
        return session_row

    def revoke_all_sessions_for_account(
        self,
        account_id: UUID,
        *,
        reason: str,
        revoked_at: datetime | None = None,
        exclude_session_ids: Iterable[UUID] = (),
    ) -> int:
        if reason not in OPERATOR_SESSION_REVOKE_REASONS:
            raise ValueError(f"unsupported revoke reason: {reason!r}")
        now = revoked_at or self.database_now()
        exclude = set(exclude_session_ids)
        rows = (
            self.db.execute(
                select(OperatorSession)
                .where(
                    OperatorSession.operator_account_id == account_id,
                    OperatorSession.revoked_at.is_(None),
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        count = 0
        for row in rows:
            if row.id in exclude:
                continue
            row.revoked_at = now
            row.revoke_reason = reason
            count += 1
        if count:
            self.db.flush()
        return count

    def list_active_sessions_for_hmac_key(
        self, hmac_key_id: str, *, for_update: bool = False
    ) -> list[OperatorSession]:
        stmt = select(OperatorSession).where(
            OperatorSession.hmac_key_id == hmac_key_id,
            OperatorSession.revoked_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        return list(self.db.execute(stmt).scalars().all())
