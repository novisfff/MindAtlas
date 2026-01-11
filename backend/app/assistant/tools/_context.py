"""工具上下文管理"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

from sqlalchemy.orm import Session

_current_db: ContextVar[Optional[Session]] = ContextVar("current_db", default=None)


def set_current_db(db: Session) -> Token[Optional[Session]]:
    return _current_db.set(db)


def reset_current_db(token: Token[Optional[Session]]) -> None:
    _current_db.reset(token)


def get_current_db() -> Session:
    db = _current_db.get()
    if db is None:
        raise RuntimeError("No database session in context")
    return db
