# Single-Operator Production Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the production authentication and authorization boundary for one self-hosted MindAtlas Operator: one-time setup authorization, exact-password verification, durable cookie sessions, CSRF, role dependencies, append-only audit, and complete browser-route protection.

**Architecture:** A new <code>app.operator_auth</code> package owns cryptography, the singleton account, sessions, request security context, dependencies, and audit. Public, browser, credential-exchange, setup, and machine routers are mounted through explicit policy classes; all ordinary browser reads require a verified session, all ordinary browser mutations additionally require the Operator role and CSRF, and OpenClaw keeps its separate Bearer-authenticated machine boundary. Initialization becomes a transaction coordinator that stages the Operator and existing core data atomically, commits once, and issues the first browser session only after that commit.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 15, <code>argon2-cffi</code>, HMAC-SHA256, React 18, TypeScript, TanStack Query, Vitest, Testing Library, Docker Compose.

## Global Constraints

- Implement from the approved clean baseline commit <code>ca925eeba569357ddb2c5c3aa63554b391efd21b</code>. If HEAD differs, inspect every intervening change and refresh exact line references before editing; do not silently reinterpret the design.
- This is a single-user, self-hosted Operator control plane. Do not add organizations, tenants, invitations, OIDC, OAuth, or caller-selected roles.
- The only production HTTP constructor of <code>OperatorPrincipal</code> is successful validation of a durable password session.
- Caller-supplied identity or role headers, environment values, CLI arguments, loopback origin, CORS, and feature flags never mint an <code>OperatorPrincipal</code>.
- The authorization vocabulary remains exactly <code>viewer</code> and <code>operator</code>; initialization creates exactly one enabled <code>operator</code>.
- Initialization requires <code>Authorization: Setup &lt;token&gt;</code>. The Setup Token never appears in a URL, query, JSON body, cookie, CLI argument, log, metric, audit payload, or committed fixture.
- The Setup Token is at least 32 UTF-8 bytes and is unusable after the singleton initialization transaction commits.
- Browser authentication after initialization uses the host-only <code>mindatlas_session</code> HttpOnly cookie.
- CSRF uses the separate browser-readable <code>mindatlas_csrf</code> cookie and <code>X-MindAtlas-CSRF</code> header.
- Session and CSRF cookies use <code>SameSite=Strict</code>, <code>Path=/</code>, no <code>Domain</code>, and <code>Secure</code> except in explicitly validated local-development HTTP mode.
- Login and setup accept JSON only and enforce the configured canonical Origin plus Fetch Metadata before checking any secret.
- CORS is explicit and non-wildcard whenever credentials are enabled.
- Passwords are exact Unicode strings: no trimming and no normalization. Minimum length is 12 Unicode code points; maximum UTF-8 size is 1024 bytes.
- Argon2id parameters are memory 64 MiB, time cost 3, parallelism 2, salt 16 bytes, and hash 32 bytes.
- Session and CSRF raw tokens are independent 256-bit operating-system CSPRNG values and are never stored in the database.
- HMAC-SHA256 uses distinct fixed domain-separation labels for session, CSRF, and request-context digests.
- The active session-MAC key ring contains one active key and at most one previous key; every decoded key is at least 32 bytes.
- Session idle expiry is 12 hours, absolute expiry is 7 days, and refresh never extends absolute expiry.
- Five failed password attempts in a database-time 15-minute window lock login for 15 database-time minutes. A successful login clears the active failure window.
- Password change revokes all sessions. Logout and revoke-all are immediately durable.
- Removing a previous HMAC key durably revokes sessions that still depend on it and appends a safe maintenance audit event.
- <code>operator_audit_event</code> is append-only; PostgreSQL must reject UPDATE and DELETE.
- Apart from setup-authorized initialization, every browser control-plane mutation requires Operator plus CSRF. Browser data mutations use the same rule.
- OpenClaw runtime endpoints retain their existing Bearer secret and narrower machine context. A session cookie cannot replace that Bearer secret, and a valid OpenClaw credential cannot satisfy an Operator dependency.
- Remove the <code>legacy_auto_completed</code> initialization behavior and response field. Existing Legacy-looking data never auto-initializes the clean product.
- Each implementation task follows red-green-refactor, ends in one independently reviewable commit, and leaves focused tests green.
- Evidence contains no password, password hash, Setup/Session/CSRF token, provider credential, raw IP address, raw User-Agent, Prompt, Entry content, Artifact body, or raw idempotency key.
- If a security requirement, singleton constraint, PostgreSQL append-only trigger, route inventory classification, or clean-baseline prerequisite cannot be proven, stop. Do not replace the gate with a warning, skip, environment bypass, or caller assertion.

---

## Prerequisites and Stable Interfaces

### Required checkpoint

Before implementation:

~~~bash
git status --short
git rev-parse HEAD
rg -n '9f3c1a7e2b40|b6e2d4f8a901|pre_ga_v1_0001|pre_ga_v1_0002' backend
cd backend && .venv/bin/alembic heads
~~~

Expected:

- <code>git status --short</code> prints nothing.
- HEAD is the approved design baseline or a reviewed descendant.
- The revision-ID scan prints no collision.
- Alembic prints exactly <code>3bd7bc4257c9 (head)</code>.

Plan 1 owns additive revision <code>9f3c1a7e2b40</code> with <code>down_revision = "3bd7bc4257c9"</code>. Plan 2 consumes that revision and creates <code>b6e2d4f8a901</code>. Plan 3 later archives both with the old lineage and generates the clean root; Plan 1 must not start that archive.

### Stable Python contracts exported to Plans 2–4

<code>backend/app/operator_auth/contracts.py</code> is the canonical definition:

~~~python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

OperatorRole = Literal["viewer", "operator"]


@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: UUID
    role: OperatorRole
    session_id: UUID
    authentication_method: Literal["password_session"] = "password_session"

    @property
    def principal_id(self) -> str:
        return str(self.operator_id)

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"

    def audit_actor(self) -> str:
        return str(self.operator_id)


@dataclass(frozen=True)
class OperatorAuthAvailability:
    available: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class IssuedSession:
    principal: OperatorPrincipal
    session_cookie_value: str
    csrf_cookie_value: str
    idle_expires_at: datetime
    absolute_expires_at: datetime
~~~

The dependency signatures are stable:

~~~python
def require_viewer_principal(...) -> OperatorPrincipal: ...
def require_operator_principal(...) -> OperatorPrincipal: ...
def require_csrf(...) -> None: ...
~~~

The version exported by <code>backend/app/operator_auth/constants.py</code> is:

~~~python
OPERATOR_AUTH_CONTRACT_VERSION = "operator-auth-v1"
~~~

Plan 2 consumes <code>OperatorAuthService.availability() -&gt; OperatorAuthAvailability</code> for readiness. Plan 4 binds <code>OPERATOR_AUTH_CONTRACT_VERSION</code> into the launch subject.

### Stable initialization seam exported to Plan 2

Plan 1 leaves one transaction owner and two explicit staging points:

~~~python
@dataclass(frozen=True)
class CoreInitializationResult:
    locale: Literal["zh", "en"]
    credential_id: UUID
    llm_model_id: UUID


class SystemInitializationService:
    def stage_core_initialization(
        self, request: InitializeSystemRequest
    ) -> CoreInitializationResult: ...

    def stage_initialization_marker(
        self, *, locale: Literal["zh", "en"], source: Literal["user"]
    ) -> None: ...


class InitializationCoordinator:
    def initialize(
        self,
        request: InitializeSystemRequest,
        *,
        setup_authorization: SetupAuthorization,
        request_context: RequestSecurityContext,
    ) -> InitializationCommitResult: ...
~~~

Plan 2 inserts its trusted seed/bootstrap stage after <code>stage_core_initialization()</code> and before <code>stage_initialization_marker()</code>, inside the same coordinator-owned transaction. No lower service may call <code>commit()</code> in that path.

### Fixed HTTP contract

| Method and path | Policy | Request | Safe response |
|---|---|---|---|
| <code>GET /health</code> | public liveness | none | process status only |
| <code>GET /api/system-settings/initialization-status</code> | public status | none | <code>initialized</code>, <code>locale</code> |
| <code>GET /api/system-settings/initialization-defaults</code> | public status | locale query | non-secret defaults |
| <code>POST /api/system-settings/initialize</code> | setup exchange | JSON, Setup header, same-origin metadata | initial session cookies plus safe completion |
| <code>POST /api/operator-auth/login</code> | credential exchange | <code>{"password": "exact secret"}</code> plus same-origin metadata | session cookies plus safe expiry/role |
| <code>GET /api/operator-auth/session</code> | optional session probe | cookies | authenticated state; no raw token |
| <code>POST /api/operator-auth/logout</code> | viewer session plus CSRF | empty JSON | clears current cookies |
| <code>POST /api/operator-auth/password</code> | Operator plus CSRF | current and new exact passwords | revokes all sessions and clears cookies |
| <code>POST /api/operator-auth/sessions/revoke-all</code> | Operator plus CSRF | bounded reason | revokes all sessions and clears cookies |

Stable failure semantics:

- 401 <code>invalid_setup_authorization</code>, <code>invalid_credentials</code>, or <code>invalid_session</code>;
- 403 <code>operator_role_required</code>, <code>csrf_rejected</code>, or <code>same_origin_required</code>;
- 409 <code>system_already_initialized</code> or <code>operator_revision_conflict</code>;
- 415 <code>json_content_type_required</code>;
- 429 <code>login_locked</code> with bounded integer <code>retryAfterSeconds</code>;
- 503 <code>operator_auth_unavailable</code>.

Errors never reveal which password, Setup Token, cookie component, or key ID was wrong.

---

## File Structure

### Create

- <code>backend/app/operator_auth/__init__.py</code> — public exports only.
- <code>backend/app/operator_auth/constants.py</code> — contract version, cookie/header names, duration and Argon2 constants, domain labels, error identifiers.
- <code>backend/app/operator_auth/contracts.py</code> — frozen Principal, availability, issued-session, setup-authorization, and request-context contracts.
- <code>backend/app/operator_auth/models.py</code> — <code>OperatorAccount</code>, <code>OperatorSession</code>, <code>OperatorAuditEvent</code>.
- <code>backend/app/operator_auth/password.py</code> — exact password validation, Argon2id hash/verify/rehash.
- <code>backend/app/operator_auth/tokens.py</code> — key-ring parsing, random token generation, cookie encoding, domain-separated HMAC.
- <code>backend/app/operator_auth/origin.py</code> — canonical Origin, Fetch Metadata, JSON content-type checks.
- <code>backend/app/operator_auth/repository.py</code> — singleton locks, database time, account/session state, lockout, revocation.
- <code>backend/app/operator_auth/audit.py</code> — safe append-only event staging and request mutation scope.
- <code>backend/app/operator_auth/service.py</code> — password authentication, session lifecycle, rotation, password change, availability.
- <code>backend/app/operator_auth/dependencies.py</code> — session, role, CSRF, setup, and credential-exchange dependencies.
- <code>backend/app/operator_auth/route_policy.py</code> — parent-router construction and policy marker inspection.
- <code>backend/app/operator_auth/schemas.py</code> — JSON request/response models.
- <code>backend/app/operator_auth/router.py</code> — login, session, logout, password, revoke-all.
- <code>backend/app/system_settings/initialization_coordinator.py</code> — the only initialization transaction owner.
- <code>backend/alembic/versions/9f3c1a7e2b40_add_single_operator_control_plane.py</code> — additive account/session/audit schema and audit immutability trigger.
- <code>backend/tests/test_operator_auth_config.py</code> — origin, CORS, setup token, key-ring config.
- <code>backend/tests/test_operator_password.py</code> — exact-secret and Argon2 contract.
- <code>backend/tests/test_operator_auth_service.py</code> — login window, session lifecycle, rotation, revoke, password revision.
- <code>backend/tests/test_operator_auth_api.py</code> — cookies, login/setup origin policy, CSRF and RBAC.
- <code>backend/tests/test_operator_auth_postgres.py</code> — singleton concurrency, constraints, DB time, append-only trigger.
- <code>backend/tests/test_route_auth_inventory.py</code> — exhaustive FastAPI route-policy classification.
- <code>backend/tests/test_operator_cli_boundary.py</code> — forged Header, env, and CLI identity fail-closed tests.
- <code>backend/scripts/verify_operator_control_plane.py</code> — fixed safe evidence runner.
- <code>frontend/src/features/operator-auth/api/operatorAuth.ts</code> — typed auth API.
- <code>frontend/src/features/operator-auth/queries.ts</code> — session query and mutations.
- <code>frontend/src/features/operator-auth/components/OperatorGate.tsx</code> — login/session gate.
- <code>frontend/src/features/operator-auth/pages/OperatorLoginPage.tsx</code> — password login form.
- <code>frontend/src/features/operator-auth/index.ts</code> — public frontend exports.
- <code>frontend/src/features/operator-auth/api/operatorAuth.test.ts</code> — cookie/CSRF and 401 client behavior.
- <code>frontend/src/features/operator-auth/components/OperatorGate.test.tsx</code> — route transitions and expiry.
- <code>frontend/src/features/operator-auth/pages/OperatorLoginPage.test.tsx</code> — exact password and generic errors.

### Modify

- <code>backend/app/config.py</code> — canonical Origin, Setup Token, session key ring, strict CORS validation.
- <code>backend/requirements.txt</code> — direct <code>argon2-cffi</code> dependency; Plan 4 later locks it exactly.
- <code>backend/.env.example</code> — names and generation guidance only; no usable secret.
- <code>backend/app/main.py</code> — explicit public/protected/machine router composition.
- <code>backend/app/database.py</code> — register auth models and request-safe transaction cleanup only if required by the dependency implementation.
- <code>backend/app/system_settings/router.py</code> — split public initialization routes from protected settings routes and issue initial cookies.
- <code>backend/app/system_settings/schemas.py</code> — add exact Operator password; remove <code>legacyAutoCompleted</code>.
- <code>backend/app/system_settings/initialization_service.py</code> — remove Legacy auto-completion and internal commit; expose staging methods.
- <code>backend/app/assistant/skills/principal.py</code> — compatibility re-export of the canonical dataclass.
- <code>backend/app/assistant/skills/admin_router.py</code> — remove Header principal and trusted mount.
- <code>backend/app/assistant/evaluation/router.py</code> — remove trusted mount and use canonical dependency.
- <code>backend/app/assistant/skills/router.py</code> — consume parent protection for base package/Profile mutations.
- <code>backend/app/openclaw_integration/router.py</code> — explicit machine dependency for runtime routes; settings stay in browser router.
- <code>backend/app/assistant/capability_calls/cli.py</code> — make reconciliation mutation unavailable to asserted CLI/env identity.
- <code>backend/tests/_db.py</code> — register auth models.
- Existing Skill/Eval/OpenClaw/initialization tests that construct trusted headers — replace with real session fixtures.
- <code>frontend/src/lib/api/client.ts</code> — same-origin credentials, CSRF injection, centralized session-expired signal.
- <code>frontend/src/features/assistant-config/api/skill-packages.ts</code> — remove Vite Operator Header support.
- <code>frontend/src/features/assistant-config/api/main-agent-profiles.ts</code> — stop attaching asserted authority.
- <code>frontend/src/features/initialization/api/systemInitialization.ts</code> — Operator password plus in-memory Setup authorization header.
- <code>frontend/src/features/initialization/pages/SystemInitializationPage.tsx</code> — collect password and Setup Token without persistence.
- <code>frontend/src/features/initialization/components/InitializationGate.tsx</code> — coordinate initialized versus authenticated states.
- <code>frontend/src/features/initialization/components/InitializationGate.test.tsx</code> — clean-only status and post-init session.
- <code>frontend/src/app/App.tsx</code> — add <code>/login</code> and Operator gate.
- Locale files under <code>frontend/src/locales/</code> — setup/login/session-expiry copy without unsupported multi-user concepts.
- <code>deploy/docker-compose.yml</code> and <code>deploy/README.md</code> — secret injection, canonical Origin, HTTPS boundary, initialization procedure.
- <code>.github/workflows/ci.yml</code> — focused PostgreSQL auth/inventory gates.

---

### Task 1: Freeze the auth contract, configuration, password, and token primitives

**Files:**

- Create: <code>backend/app/operator_auth/__init__.py</code>
- Create: <code>backend/app/operator_auth/constants.py</code>
- Create: <code>backend/app/operator_auth/contracts.py</code>
- Create: <code>backend/app/operator_auth/password.py</code>
- Create: <code>backend/app/operator_auth/tokens.py</code>
- Create: <code>backend/app/operator_auth/origin.py</code>
- Create: <code>backend/tests/test_operator_auth_config.py</code>
- Create: <code>backend/tests/test_operator_password.py</code>
- Modify: <code>backend/app/config.py</code>
- Modify: <code>backend/requirements.txt</code>
- Modify: <code>backend/.env.example</code>

**Interfaces:**

- Consumes: existing <code>Settings</code>, <code>ApiException</code>, and Python 3.11.
- Produces: <code>OperatorPrincipal</code>, <code>OperatorAuthAvailability</code>, <code>SessionMacKeyRing</code>, <code>PasswordService</code>, <code>require_json_same_origin()</code>, and all fixed constants used by the remaining tasks.

- [ ] **Step 1: Add the direct password-hashing dependency and failing exact-secret tests**

Add <code>argon2-cffi</code> as a direct line in <code>backend/requirements.txt</code>. Do not pin the transitive graph here; Plan 4 owns generated Python 3.11 locks.

Create tests:

~~~python
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
~~~

- [ ] **Step 2: Run the password tests and confirm RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/test_operator_password.py -q
~~~

Expected: collection fails because <code>app.operator_auth.password</code> does not exist.

- [ ] **Step 3: Implement the immutable contracts and Argon2id policy**

Use exact constants:

~~~python
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
~~~

Implement the password service:

~~~python
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError


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
        if len(secret) < 12:
            raise PasswordPolicyError("password must contain at least 12 Unicode code points")
        if len(secret.encode("utf-8")) > 1024:
            raise PasswordPolicyError("password must not exceed 1024 UTF-8 bytes")

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
~~~

The input string must not pass through <code>strip()</code>, Unicode normalization, case folding, or lossy encoding at any layer.

- [ ] **Step 4: Write failing key-ring, token, Origin, and CORS tests**

~~~python
import base64
import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.operator_auth.tokens import SessionMacKeyRing


def _key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def test_key_ring_accepts_active_plus_one_previous() -> None:
    ring = SessionMacKeyRing.parse(
        active_key_id="k2",
        encoded_json=json.dumps({"k1": _key(1), "k2": _key(2)}),
    )
    assert ring.active_key_id == "k2"
    assert tuple(ring.keys) == ("k1", "k2")


@pytest.mark.parametrize(
    ("active", "payload"),
    [
        ("missing", {"k1": _key(1)}),
        ("k1", {"k1": base64.b64encode(b"x" * 31).decode("ascii")}),
        ("k1", {"k1": _key(1), "k2": _key(2), "k3": _key(3)}),
    ],
)
def test_key_ring_rejects_invalid_shape(active: str, payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        SessionMacKeyRing.parse(active_key_id=active, encoded_json=json.dumps(payload))


def test_production_rejects_wildcard_or_origin_mismatch() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            MINDATLAS_CANONICAL_ORIGIN="https://atlas.example",
            CORS_ORIGINS="*",
        )
~~~

- [ ] **Step 5: Run the new config tests and confirm RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/test_operator_auth_config.py -q
~~~

Expected: imports or assertions fail because key-ring and canonical-origin settings are absent.

- [ ] **Step 6: Implement strict key-ring and configuration parsing**

Add these secret-bearing settings without repository defaults:

~~~python
from pydantic import SecretStr

initial_setup_token: SecretStr | None = Field(
    default=None, alias="MINDATLAS_INITIAL_SETUP_TOKEN"
)
canonical_origin: str = Field(default="", alias="MINDATLAS_CANONICAL_ORIGIN")
session_hmac_active_key_id: str = Field(
    default="", alias="MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID"
)
session_hmac_keys: SecretStr | None = Field(
    default=None, alias="MINDATLAS_SESSION_HMAC_KEYS"
)
~~~

Validate production/staging as follows:

~~~python
origin = self.canonical_origin.strip()
cors = self.cors_origins_list()
if self.app_env in {"production", "staging"}:
    if not origin.startswith("https://"):
        raise ValueError("MINDATLAS_CANONICAL_ORIGIN must be HTTPS")
    if "*" in cors or origin not in cors:
        raise ValueError("credentialed CORS must contain the exact canonical origin")
token = self.initial_setup_token.get_secret_value() if self.initial_setup_token else ""
if token and len(token.encode("utf-8")) < 32:
    raise ValueError("MINDATLAS_INITIAL_SETUP_TOKEN must be at least 32 UTF-8 bytes")
~~~

The settings object deliberately remains constructible when Setup or session-MAC secrets are absent so <code>/health</code> can report process liveness. <code>OperatorAuthService.availability()</code> then returns <code>operator_auth_unavailable</code>, initialization/login reject with 503, and Plan 2 maps it to readiness reason <code>operator_auth_unavailable</code>. Missing secrets must never generate ephemeral replacements.

Parse key JSON with strict Base64 validation:

~~~python
raw = base64.b64decode(encoded, validate=True)
if len(raw) < 32:
    raise ValueError("each session HMAC key must decode to at least 32 bytes")
if len(keys) not in {1, 2}:
    raise ValueError("session HMAC key ring must contain active plus at most one previous key")
if active_key_id not in keys:
    raise ValueError("active session HMAC key id is not present")
~~~

Do not include any secret value in exception text or object representation.

In <code>backend/app/main.py</code>, replace wildcard credentialed CORS headers/methods with the explicit contract:

~~~python
allow_methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
allow_headers=[
    "Accept",
    "Authorization",
    "Content-Type",
    "X-MindAtlas-CSRF",
    "X-MindAtlas-Locale",
    "X-Request-ID",
]
~~~

- [ ] **Step 7: Implement token encoding and domain-separated HMAC**

Use URL-safe Base64 without padding. Session cookies contain a version, non-secret session UUID, and raw random token so the row can be located without storing the token:

~~~python
def issue_raw_session_cookie(session_id: UUID) -> tuple[str, bytes]:
    raw = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"v1.{session_id.hex}.{encoded}", raw


def digest_session(*, key: bytes, session_id: UUID, raw: bytes) -> str:
    message = SESSION_HMAC_LABEL + session_id.bytes + raw
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def digest_csrf(*, key: bytes, session_id: UUID, raw: bytes) -> str:
    message = CSRF_HMAC_LABEL + session_id.bytes + raw
    return hmac.new(key, message, hashlib.sha256).hexdigest()
~~~

Parsing rejects unknown versions, invalid UUIDs, non-canonical Base64, and any token not exactly 32 bytes. Comparisons use <code>hmac.compare_digest</code>.

- [ ] **Step 8: Implement JSON/same-origin policy**

The shared policy rejects credentials before any password or Setup Token comparison:

~~~python
def require_json_same_origin(request: Request, *, canonical_origin: str) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type != "application/json":
        raise ApiException(status_code=415, code=41510, message="json_content_type_required")
    if not canonical_origin or not secrets.compare_digest(
        request.headers.get("origin", ""), canonical_origin
    ):
        raise ApiException(status_code=403, code=40312, message="same_origin_required")
    if request.headers.get("sec-fetch-site", "").lower() != "same-origin":
        raise ApiException(status_code=403, code=40312, message="same_origin_required")
~~~

Tests must show that missing Origin, <code>Sec-Fetch-Site: cross-site</code>, a sibling subdomain, and a lookalike port fail before a password verifier spy is called.

- [ ] **Step 9: Run focused tests and commit**

~~~bash
cd backend
.venv/bin/python -m pytest \
  tests/test_operator_password.py \
  tests/test_operator_auth_config.py -q
cd ..
git add backend/app/operator_auth backend/app/config.py backend/requirements.txt backend/.env.example backend/tests/test_operator_password.py backend/tests/test_operator_auth_config.py
git commit -m "feat(auth): define single-operator security primitives"
~~~

Expected: focused tests pass; the commit contains no secret values.

---

### Task 2: Add singleton account, durable sessions, and append-only audit schema

**Files:**

- Create: <code>backend/app/operator_auth/models.py</code>
- Create: <code>backend/alembic/versions/9f3c1a7e2b40_add_single_operator_control_plane.py</code>
- Create: <code>backend/tests/test_operator_auth_postgres.py</code>
- Modify: <code>backend/tests/_db.py</code>
- Modify: <code>backend/alembic/env.py</code>

**Interfaces:**

- Consumes: Task 1 constants and current Alembic head <code>3bd7bc4257c9</code>.
- Produces: <code>OperatorAccount</code>, <code>OperatorSession</code>, <code>OperatorAuditEvent</code>, singleton constraints, indexes, and PostgreSQL audit immutability function/trigger.

- [ ] **Step 1: Write failing model metadata and PostgreSQL migration tests**

~~~python
def test_operator_schema_has_required_constraints(pg_migrator) -> None:
    pg_migrator.upgrade("9f3c1a7e2b40")
    assert pg_migrator.unique_columns("operator_account") == {("singleton_key",)}
    assert pg_migrator.has_check(
        "operator_account", "ck_operator_account_singleton_key"
    )
    assert pg_migrator.has_unique("operator_session", ("token_digest",))


def test_operator_audit_is_append_only(pg_session) -> None:
    event = seed_operator_audit_event(pg_session)
    pg_session.commit()
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text("UPDATE operator_audit_event SET event_type='changed' WHERE id=:id"),
            {"id": event.id},
        )
        pg_session.commit()
    pg_session.rollback()
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text("DELETE FROM operator_audit_event WHERE id=:id"), {"id": event.id}
        )
        pg_session.commit()
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_operator_auth_postgres.py -q
~~~

Expected: tests fail because revision <code>9f3c1a7e2b40</code> and tables do not exist. If the PostgreSQL URL is absent, stop; this task is not eligible for a SQLite-only result.

- [ ] **Step 3: Implement exact SQLAlchemy models**

Use these durable fields:

~~~python
class OperatorAccount(Base):
    __tablename__ = "operator_account"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton_key = Column(String(32), nullable=False, default="operator")
    role = Column(String(16), nullable=False, default="operator")
    password_hash = Column(Text, nullable=False)
    password_revision = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)
    failed_login_window_started_at = Column(DateTime(timezone=True), nullable=True)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class OperatorSession(Base):
    __tablename__ = "operator_session"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_account_id = Column(
        UUID(as_uuid=True), ForeignKey("operator_account.id", ondelete="CASCADE"), nullable=False
    )
    token_digest = Column(String(64), nullable=False, unique=True)
    csrf_digest = Column(String(64), nullable=False)
    hmac_key_id = Column(String(64), nullable=False)
    password_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    idle_expires_at = Column(DateTime(timezone=True), nullable=False)
    absolute_expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(64), nullable=True)
    request_digest = Column(String(64), nullable=False)
    user_agent_digest = Column(String(64), nullable=False)
    network_digest = Column(String(64), nullable=False)


class OperatorAuditEvent(Base):
    __tablename__ = "operator_audit_event"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    event_type = Column(String(64), nullable=False)
    outcome = Column(String(32), nullable=False)
    operator_id = Column(UUID(as_uuid=True), nullable=True)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    request_id = Column(String(128), nullable=False)
    request_digest = Column(String(64), nullable=False)
    user_agent_digest = Column(String(64), nullable=False)
    network_digest = Column(String(64), nullable=False)
    reason_code = Column(String(64), nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict)
~~~

Checks require singleton key <code>operator</code>, role in <code>viewer/operator</code>, positive password revision, nonnegative failure count, 64-lowercase-hex digests, absolute expiry after creation, idle expiry no later than absolute expiry, and bounded revoke reasons.

- [ ] **Step 4: Implement the additive Alembic revision**

The revision header is exact:

~~~python
revision = "9f3c1a7e2b40"
down_revision = "3bd7bc4257c9"
branch_labels = None
depends_on = None
~~~

Create the three tables, indexes for active session lookup and account lockout, and the append-only trigger:

~~~sql
CREATE OR REPLACE FUNCTION mindatlas_reject_operator_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'operator_audit_event is append-only'
    USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER trg_operator_audit_event_append_only
BEFORE UPDATE OR DELETE ON operator_audit_event
FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_operator_audit_mutation();
~~~

Downgrade is allowed only on an empty, uninitialized non-production test database and requires <code>MINDATLAS_TEST_DESTRUCTIVE_DOWNGRADE=1</code>. Otherwise it raises <code>operator_auth_downgrade_blocked</code>. Do not provide a force flag.

- [ ] **Step 5: Register models and test sole-head behavior**

Import <code>app.operator_auth.models</code> in Alembic metadata loading and <code>backend/tests/_db.py</code>. Then run:

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic upgrade 9f3c1a7e2b40
.venv/bin/alembic current
~~~

Expected:

- one head: <code>9f3c1a7e2b40 (head)</code>;
- current: <code>9f3c1a7e2b40 (head)</code>;
- no B2 maintenance acknowledgement is introduced by this additive revision.

- [ ] **Step 6: Run PostgreSQL constraints and commit**

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_operator_auth_postgres.py -q
git add backend/app/operator_auth/models.py backend/alembic/versions/9f3c1a7e2b40_add_single_operator_control_plane.py backend/alembic/env.py backend/tests/_db.py backend/tests/test_operator_auth_postgres.py
git commit -m "feat(auth): persist operator sessions and immutable audit"
~~~

Expected: all tests pass against PostgreSQL; update and delete attempts fail with SQLSTATE <code>55000</code>.

---

### Task 3: Implement database-time lockout, account repository, and safe audit staging

**Files:**

- Create: <code>backend/app/operator_auth/repository.py</code>
- Create: <code>backend/app/operator_auth/audit.py</code>
- Create: <code>backend/tests/test_operator_auth_service.py</code>

**Interfaces:**

- Consumes: Tasks 1–2 contracts, password service, and models.
- Produces: <code>OperatorRepository.lock_initialization()</code>, <code>database_now()</code>, account lockout transitions, session persistence/revocation methods, and <code>OperatorAuditRepository.append()</code>.

- [ ] **Step 1: Write failing concurrent and database-clock tests**

~~~python
def test_fifth_failure_locks_for_database_time(repository, frozen_db_clock) -> None:
    account = repository.seed_account(password="correct horse battery")
    for index in range(4):
        state = repository.record_login_failure(account.id)
        assert state.locked_until is None
    state = repository.record_login_failure(account.id)
    assert state.failed_login_count == 5
    assert state.locked_until == frozen_db_clock.now + timedelta(minutes=15)


def test_failure_after_window_starts_new_window(repository, frozen_db_clock) -> None:
    account = repository.seed_account(password="correct horse battery")
    repository.record_login_failure(account.id)
    frozen_db_clock.advance(minutes=16)
    state = repository.record_login_failure(account.id)
    assert state.failed_login_count == 1


def test_initialization_lock_serializes_two_postgres_transactions(pg_session_factory) -> None:
    results = run_two_initializers(pg_session_factory)
    assert sorted(results) == ["committed", "system_already_initialized"]
    assert count_enabled_operator_accounts(pg_session_factory) == 1
~~~

- [ ] **Step 2: Confirm RED on PostgreSQL**

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_operator_auth_service.py \
  backend/tests/test_operator_auth_postgres.py -q
~~~

Expected: repository imports fail.

- [ ] **Step 3: Implement database time and singleton locking**

Use PostgreSQL transaction advisory lock plus row locking:

~~~python
INITIALIZATION_LOCK_KEY = 0x4D_41_4F_50


def database_now(self) -> datetime:
    return self.db.execute(select(func.now())).scalar_one()


def lock_initialization(self) -> None:
    if self.db.bind and self.db.bind.dialect.name == "postgresql":
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
~~~

The initialization coordinator must call this only inside its outer transaction and recheck both initialization marker and singleton account after acquiring it.

- [ ] **Step 4: Implement lockout transitions under row lock**

Use <code>SELECT ... FOR UPDATE</code>. Never use application wall clock:

~~~python
account = self.db.execute(
    select(OperatorAccount)
    .where(OperatorAccount.singleton_key == "operator")
    .with_for_update()
).scalar_one_or_none()
now = self.database_now()
if account.failed_login_window_started_at is None or (
    now - account.failed_login_window_started_at
).total_seconds() >= LOGIN_WINDOW_SECONDS:
    account.failed_login_window_started_at = now
    account.failed_login_count = 1
else:
    account.failed_login_count += 1
if account.failed_login_count >= LOGIN_FAILURE_LIMIT:
    account.locked_until = now + timedelta(seconds=LOGIN_LOCK_SECONDS)
~~~

Successful authentication sets failure count to zero and clears both window start and lock.

- [ ] **Step 5: Implement safe append-only audit**

The append method takes enumerated safe metadata, not arbitrary request bodies:

~~~python
def append(
    self,
    *,
    event_type: OperatorAuditEventType,
    outcome: Literal["succeeded", "rejected", "failed"],
    context: RequestSecurityContext,
    operator_id: UUID | None,
    session_id: UUID | None,
    reason_code: str | None = None,
    metadata: Mapping[str, str | int | bool | None] = MappingProxyType({}),
) -> OperatorAuditEvent:
    row = OperatorAuditEvent(
        event_type=event_type,
        outcome=outcome,
        occurred_at=self.repository.database_now(),
        operator_id=operator_id,
        session_id=session_id,
        request_id=context.request_id,
        request_digest=context.request_digest,
        user_agent_digest=context.user_agent_digest,
        network_digest=context.network_digest,
        reason_code=reason_code,
        metadata_json=dict(metadata),
    )
    self.db.add(row)
    self.db.flush()
    return row
~~~

Allowlisted event types include account initialized, login succeeded/rejected/locked, session created/revoked/expired/key-revoked, logout, password changed, revoke-all, setup rejected, CSRF rejected, RBAC rejected, and control-plane mutation committed.

- [ ] **Step 6: Run focused tests and commit**

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_operator_auth_service.py \
  backend/tests/test_operator_auth_postgres.py -q
git add backend/app/operator_auth/repository.py backend/app/operator_auth/audit.py backend/tests/test_operator_auth_service.py backend/tests/test_operator_auth_postgres.py
git commit -m "feat(auth): enforce database-time operator lockout"
~~~

Expected: all lockout, concurrency, and audit tests pass.

---

### Task 4: Implement restart-stable sessions, CSRF validation, rotation, and revocation

**Files:**

- Create: <code>backend/app/operator_auth/service.py</code>
- Modify: <code>backend/tests/test_operator_auth_service.py</code>
- Modify: <code>backend/app/operator_auth/repository.py</code>

**Interfaces:**

- Consumes: repository, key ring, password service, request context.
- Produces: <code>OperatorAuthService.authenticate_password()</code>, <code>issue_session()</code>, <code>resolve_session()</code>, <code>verify_csrf()</code>, <code>change_password()</code>, <code>revoke_current()</code>, <code>revoke_all()</code>, <code>revoke_unverifiable_sessions()</code>, and <code>availability()</code>.

- [ ] **Step 1: Add failing session lifecycle tests**

~~~python
def test_restart_with_same_key_keeps_session(session_factory, key_ring) -> None:
    issued = make_service(session_factory(), key_ring).login("correct horse battery", CTX)
    restarted = make_service(session_factory(), key_ring)
    principal = restarted.resolve_session(issued.session_cookie_value, CTX)
    assert principal.session_id == issued.principal.session_id


def test_previous_key_is_rotated_on_successful_request(session_factory) -> None:
    issued = issue_with_key(session_factory, key_id="old")
    result = resolve_with_keys(session_factory, active="new", previous="old", issued=issued)
    assert result.rotated_cookie is not None
    assert stored_key_id(session_factory, issued.principal.session_id) == "new"


def test_removed_previous_key_revokes_dependent_sessions(session_factory) -> None:
    issued = issue_with_key(session_factory, key_id="old")
    service = make_service(session_factory(), ring_with_only("new"))
    count = service.revoke_unverifiable_sessions(context=MAINTENANCE_CTX)
    assert count == 1
    assert service.resolve_session(issued.session_cookie_value, CTX) is None


def test_password_revision_invalidates_all_sessions(auth_service) -> None:
    first = auth_service.login("correct horse battery", CTX)
    second = auth_service.login("correct horse battery", CTX2)
    auth_service.change_password(
        principal=first.principal,
        current_password="correct horse battery",
        new_password="a newer exact secret!",
        context=CTX,
    )
    assert auth_service.resolve_session(first.session_cookie_value, CTX) is None
    assert auth_service.resolve_session(second.session_cookie_value, CTX2) is None
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/test_operator_auth_service.py -q
~~~

Expected: session service methods are missing.

- [ ] **Step 3: Implement issue and resolve**

Session issuance uses database time and one transaction:

~~~python
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
        key=self.key_ring.active_key, session_id=session_id, raw=raw_session
    ),
    csrf_digest=digest_csrf(
        key=self.key_ring.active_key, session_id=session_id, raw=raw_csrf
    ),
    hmac_key_id=self.key_ring.active_key_id,
    password_revision=account.password_revision,
    created_at=now,
    last_seen_at=now,
    idle_expires_at=idle,
    absolute_expires_at=absolute,
    request_digest=context.request_digest,
    user_agent_digest=context.user_agent_digest,
    network_digest=context.network_digest,
)
~~~

Resolution verifies active state, account enabled, password revision, idle and absolute expiry, known key ID, token digest, and request-context policy. Invalid/expired sessions are revoked durably with a bounded reason. A successful touch sets:

~~~python
row.last_seen_at = now
row.idle_expires_at = min(
    now + timedelta(seconds=SESSION_IDLE_SECONDS),
    row.absolute_expires_at,
)
~~~

- [ ] **Step 4: Implement CSRF and active-key rotation**

CSRF verification decodes the browser cookie, recomputes the row’s keyed digest, and compares both the cookie and header:

~~~python
if not hmac.compare_digest(csrf_cookie_value, csrf_header_value):
    raise CsrfRejected
expected = digest_csrf(key=key, session_id=row.id, raw=decode_csrf(csrf_cookie_value))
if not hmac.compare_digest(expected, row.csrf_digest):
    raise CsrfRejected
~~~

When a previous-key session supplies both valid raw values, recompute both digests under the active key, update <code>hmac_key_id</code>, and return replacement cookie values with unchanged absolute expiry. Never extend absolute expiry during rotation.

- [ ] **Step 5: Implement login, rehash, and revocation semantics**

Authentication order is:

1. lock singleton account;
2. check database-time lock;
3. verify Argon2id using the generic failure response;
4. record failure/lock or clear window;
5. rehash on successful verification when parameters drift;
6. issue session and append audit;
7. commit once.

Password change verifies the exact current password, writes a new Argon2id hash, increments <code>password_revision</code>, and revokes every active session in the same transaction. Logout and revoke-all update rows and append audit before commit.

- [ ] **Step 6: Add expiry and clock-boundary vectors**

Cover:

- idle expiry at exactly 12 hours;
- absolute expiry at exactly 7 days;
- refresh at 6 days 23 hours never crossing absolute expiry;
- revoked session rejection after process restart;
- malformed cookie rejection without database exception;
- unknown key ID rejection;
- disabled account rejection;
- constant generic login failure for missing/disabled/wrong-password states.

- [ ] **Step 7: Run focused tests and commit**

~~~bash
cd backend
.venv/bin/python -m pytest tests/test_operator_auth_service.py -q
cd ..
git add backend/app/operator_auth/service.py backend/app/operator_auth/repository.py backend/tests/test_operator_auth_service.py
git commit -m "feat(auth): add durable operator session lifecycle"
~~~

Expected: all service tests pass, including restart and key-rotation vectors.

---

### Task 5: Expose secure auth HTTP dependencies, cookies, and account operations

**Files:**

- Create: <code>backend/app/operator_auth/schemas.py</code>
- Create: <code>backend/app/operator_auth/dependencies.py</code>
- Create: <code>backend/app/operator_auth/router.py</code>
- Create: <code>backend/tests/test_operator_auth_api.py</code>
- Modify: <code>backend/app/operator_auth/__init__.py</code>
- Modify: <code>backend/app/main.py</code>

**Interfaces:**

- Consumes: Task 4 service.
- Produces: canonical FastAPI dependencies, auth routes, cookie helpers, and the only HTTP session-to-Principal conversion.

- [ ] **Step 1: Write failing login/cookie/CSRF/RBAC tests**

~~~python
def test_login_sets_exact_cookie_contract(client, initialized_operator, origin_headers) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": "correct horse battery"},
        headers=origin_headers,
    )
    assert response.status_code == 200
    session = response.cookies.get("mindatlas_session")
    csrf = response.cookies.get("mindatlas_csrf")
    assert session and csrf and session != csrf
    assert "HttpOnly" in response.headers.get_list("set-cookie")[0]
    assert all("SameSite=strict" in item for item in response.headers.get_list("set-cookie"))
    assert all("Domain=" not in item for item in response.headers.get_list("set-cookie"))


def test_mutation_requires_cookie_header_pair(authenticated_client) -> None:
    response = authenticated_client.put(
        "/api/system-settings/locale", json={"locale": "en"}
    )
    assert response.status_code == 403
    assert response.json()["message"] == "csrf_rejected"


def test_forged_operator_headers_never_authenticate(client) -> None:
    response = client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers={
            "X-MindAtlas-Operator-Id": "forged",
            "X-MindAtlas-Operator-Role": "operator",
        },
    )
    assert response.status_code == 401
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/test_operator_auth_api.py -q
~~~

Expected: routes or dependencies are absent.

- [ ] **Step 3: Define request and response schemas without secret echo**

~~~python
class OperatorLoginRequest(CamelModel):
    password: str = Field(min_length=1, max_length=1024)


class OperatorSessionResponse(CamelModel):
    authenticated: bool
    role: Literal["operator"] | None = None
    idle_expires_at: datetime | None = Field(default=None, alias="idleExpiresAt")
    absolute_expires_at: datetime | None = Field(default=None, alias="absoluteExpiresAt")


class OperatorPasswordChangeRequest(CamelModel):
    current_password: str = Field(alias="currentPassword", min_length=1, max_length=1024)
    new_password: str = Field(alias="newPassword", min_length=1, max_length=1024)


class OperatorRevokeAllRequest(CamelModel):
    reason: str = Field(min_length=1, max_length=256)
~~~

Do not serialize password fields, session IDs, Operator IDs, raw tokens, token digests, password revision, key IDs, or request fingerprints in unauthenticated responses.

- [ ] **Step 4: Implement the canonical dependencies**

The principal constructors use the cookie only:

~~~python
def require_viewer_principal(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OperatorPrincipal:
    value = request.cookies.get(SESSION_COOKIE_NAME)
    resolved = build_operator_auth_service(db, settings).resolve_session(
        value, request_security_context(request, settings)
    )
    if resolved is None:
        raise ApiException(status_code=401, code=40110, message="invalid_session")
    request.state.operator_session_resolution = resolved
    return resolved.principal


def require_operator_principal(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
) -> OperatorPrincipal:
    if principal.role != "operator":
        raise ApiException(status_code=403, code=40311, message="operator_role_required")
    return principal
~~~

<code>require_csrf()</code> consumes the cached resolved session, both CSRF values, exact Origin, and <code>Sec-Fetch-Site: same-origin</code>. No dependency reads <code>X-MindAtlas-Operator-Id</code> or <code>X-MindAtlas-Operator-Role</code>.

<code>request_security_context()</code> hashes the server-generated request ID, the raw User-Agent, and <code>request.client.host</code> with the active context-HMAC label. It never trusts <code>X-Forwarded-For</code> unless a separately configured trusted-ingress middleware has already replaced <code>request.client</code>, and it never persists those raw values.

- [ ] **Step 5: Implement secure cookie helpers and routes**

Cookie writes are centralized:

~~~python
response.set_cookie(
    SESSION_COOKIE_NAME,
    issued.session_cookie_value,
    httponly=True,
    secure=cookie_policy.secure,
    samesite="strict",
    path="/",
    max_age=None,
)
response.set_cookie(
    CSRF_COOKIE_NAME,
    issued.csrf_cookie_value,
    httponly=False,
    secure=cookie_policy.secure,
    samesite="strict",
    path="/",
    max_age=None,
)
~~~

Login and setup use <code>Cache-Control: no-store</code>. Logout, password change, revoke-all, and invalid-session cleanup emit expired cookies with identical path/SameSite/Secure attributes.

- [ ] **Step 6: Add negative origin, content type, lockout, and audit assertions**

Test JSON-only login, missing and cross-site metadata, generic wrong-password response, bounded retry-after, viewer mutation 403, missing/invalid CSRF 403, logout durability, password-change cookie clearing, and audit metadata allowlisting.

- [ ] **Step 7: Run focused API tests and commit**

~~~bash
cd backend
.venv/bin/python -m pytest \
  tests/test_operator_auth_api.py \
  tests/test_operator_auth_service.py -q
cd ..
git add backend/app/operator_auth backend/app/main.py backend/tests/test_operator_auth_api.py
git commit -m "feat(auth): expose secure operator cookie sessions"
~~~

Expected: all auth API and service tests pass.

---

### Task 6: Make clean-only initialization one atomic Operator-owned transaction

**Files:**

- Create: <code>backend/app/system_settings/initialization_coordinator.py</code>
- Modify: <code>backend/app/system_settings/initialization_service.py</code>
- Modify: <code>backend/app/system_settings/router.py</code>
- Modify: <code>backend/app/system_settings/schemas.py</code>
- Modify: <code>backend/tests/test_system_initialization_service.py</code>
- Modify: <code>backend/tests/test_operator_auth_api.py</code>
- Modify: <code>frontend/src/features/initialization/api/systemInitialization.ts</code> only in Task 11; do not mix frontend work into this commit.

**Interfaces:**

- Consumes: Tasks 3–5 setup authorization, account staging, audit, and cookie issuance.
- Produces: <code>CoreInitializationResult</code>, <code>InitializationCoordinator</code>, clean-only status, atomic first account/core state, and post-commit initial session.

- [ ] **Step 1: Write failing clean-only and rollback tests**

~~~python
def test_existing_data_never_auto_completes_initialization(session) -> None:
    session.add(Entry(content="preexisting development data"))
    session.commit()
    status = SystemInitializationService(session).get_initialization_status()
    assert status.initialized is False
    assert not hasattr(status, "legacy_auto_completed")


def test_failed_core_stage_rolls_back_operator_and_marker(
    coordinator, session, valid_request, monkeypatch
) -> None:
    monkeypatch.setattr(
        SystemInitializationService,
        "_align_relation_types",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    with pytest.raises(RuntimeError, match="injected"):
        coordinator.initialize(
            valid_request,
            setup_authorization=VALID_SETUP,
            request_context=CTX,
        )
    assert session.query(OperatorAccount).count() == 0
    assert initialization_marker(session) is None


def test_concurrent_initialization_has_one_winner(pg_client_factory) -> None:
    responses = post_concurrently(pg_client_factory, count=2)
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert count_enabled_operators(pg_client_factory) == 1
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_system_initialization_service.py \
  backend/tests/test_operator_auth_api.py -q
~~~

Expected: Legacy auto-completion assertions fail and initialization accepts neither Operator password nor Setup authorization.

- [ ] **Step 3: Remove Legacy auto-completion code and response shape**

Delete:

- <code>_LEGACY_ZH_SEEDED_ENTRY_TYPES</code>;
- <code>_entry_types_match_defaults_snapshot()</code> only if no clean behavior uses it;
- <code>_entry_types_have_customizations()</code>;
- <code>_has_existing_entries()</code>;
- <code>_has_existing_ai_configuration()</code>;
- <code>_should_auto_complete_legacy()</code>;
- every <code>source="legacy_auto_completed"</code> branch;
- <code>legacy_auto_completed</code> / <code>legacyAutoCompleted</code> from backend and frontend contracts.

Status becomes:

~~~python
class InitializationStatusResponse(CamelModel):
    initialized: bool
    locale: SystemLocale
~~~

An old development database without a clean marker remains uninitialized and is not silently mutated.

- [ ] **Step 4: Split staging from committing**

Refactor the existing method:

~~~python
def stage_core_initialization(
    self, request: InitializeSystemRequest
) -> CoreInitializationResult:
    locale = require_supported_locale(request.locale)
    self._upsert_locale(locale)
    credential = self._create_ai_credential(
        name=request.ai_credential.name.strip(),
        base_url=request.ai_credential.base_url.strip(),
        api_key=request.ai_credential.api_key.strip(),
    )
    model = self._create_llm_model(
        credential_id=credential.id,
        model_name=request.llm_model.name.strip(),
    )
    self._bind_llm_model(model.id)
    self._align_entry_types(locale, request)
    self._align_relation_types(locale)
    self._stage_assistant_catalog_and_runtime_config(locale, request)
    return CoreInitializationResult(
        locale=locale,
        credential_id=credential.id,
        llm_model_id=model.id,
    )
~~~

No function called by this method may commit. Existing service calls use <code>commit=False</code>. Cache clearing and scheduler sync move to an <code>after_commit()</code> hook.

- [ ] **Step 5: Implement coordinator ownership**

The coordinator sequence is exact:

~~~python
with self.db.begin():
    repository.lock_initialization()
    repository.assert_uninitialized()
    account = account_service.stage_initial_account(request.operator_password)
    core = SystemInitializationService(self.db).stage_core_initialization(request)
    SystemInitializationService(self.db).stage_initialization_marker(
        locale=core.locale, source="user"
    )
    audit.append(
        event_type="operator_account_initialized",
        outcome="succeeded",
        context=request_context,
        operator_id=account.id,
        session_id=None,
    )
commit_result = InitializationCommitResult(
    operator_account_id=account.id,
    locale=core.locale,
    llm_model_id=core.llm_model_id,
)
~~~

After successful commit, clear caches/sync scheduler and open a new transaction to issue the first session. If session issuance fails, the system remains correctly initialized and the Operator can use normal login; return <code>operator_auth_unavailable</code> without rerunning setup.

- [ ] **Step 6: Require Setup authorization before parsing or validating setup JSON**

Add <code>operatorPassword</code> to JSON. Do not rely on FastAPI’s relative ordering between a typed body parameter and Header dependencies. Use one async dependency that checks content type, Origin, Fetch Metadata, and the Setup Header before it calls <code>await request.json()</code> and <code>InitializeSystemRequest.model_validate()</code>:

~~~python
@dataclass(frozen=True)
class AuthorizedInitializationRequest:
    payload: InitializeSystemRequest
    setup: SetupAuthorization
    context: RequestSecurityContext


async def parse_authorized_initialization_request(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthorizedInitializationRequest:
    require_json_same_origin(request, canonical_origin=settings.canonical_origin)
    setup = verify_setup_header(
        request.headers.get("authorization"),
        configured_token=settings.initial_setup_token,
    )
    raw = await request.json()
    payload = InitializeSystemRequest.model_validate(raw)
    return AuthorizedInitializationRequest(
        payload=payload,
        setup=setup,
        context=request_security_context(request, settings),
    )


@setup_router.post("/initialize")
def initialize_system(
    response: Response,
    authorized: AuthorizedInitializationRequest = Depends(
        parse_authorized_initialization_request
    ),
    db: Session = Depends(get_db),
) -> ApiResponse:
    result = InitializationCoordinator(db).initialize(
        authorized.payload,
        setup_authorization=authorized.setup,
        request_context=authorized.context,
    )
    issued = build_operator_auth_service(db).issue_initial_session(
        result.operator_account_id, authorized.context
    )
    set_operator_cookies(response, issued)
    return ApiResponse.ok(result.to_response())
~~~

Dependency tests spy on body-domain validation and prove invalid Setup authorization returns 401 first.

- [ ] **Step 7: Run focused backend tests and commit**

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_system_initialization_service.py \
  backend/tests/test_operator_auth_api.py \
  backend/tests/test_operator_auth_postgres.py -q
git add backend/app/system_settings backend/tests/test_system_initialization_service.py backend/tests/test_operator_auth_api.py backend/tests/test_operator_auth_postgres.py
git commit -m "feat(setup): initialize one operator atomically"
~~~

Expected: one concurrent winner, full rollback on injected failure, and clean-only status.

---

### Task 7: Mount every browser route behind one verified policy boundary

**Files:**

- Create: <code>backend/app/operator_auth/route_policy.py</code>
- Create: <code>backend/tests/test_route_auth_inventory.py</code>
- Modify: <code>backend/app/main.py</code>
- Modify: <code>backend/app/system_settings/router.py</code>
- Modify: <code>backend/app/openclaw_integration/router.py</code>
- Modify: all router imports required to remove conditional mount helpers.

**Interfaces:**

- Consumes: canonical dependencies from Task 5.
- Produces: <code>public_router</code>, <code>credential_exchange_router</code>, <code>setup_router</code>, <code>protected_browser_router</code>, <code>machine_router</code>, and exhaustive route-policy evidence.

- [ ] **Step 1: Write the failing exhaustive route inventory**

Every non-framework route receives exactly one policy marker:

~~~python
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def test_every_application_route_has_exact_policy(app) -> None:
    for route in application_routes(app):
        markers = policy_markers(route)
        assert len(markers) == 1, route_identity(route)
        if route.methods & UNSAFE:
            assert next(iter(markers)) in {
                "credential_exchange",
                "setup_initialization",
                "protected_browser",
                "authenticated_machine",
            }


def test_only_setup_and_login_are_unsafe_without_existing_session(app) -> None:
    exemptions = {
        ("POST", "/api/system-settings/initialize", "setup_initialization"),
        ("POST", "/api/operator-auth/login", "credential_exchange"),
    }
    assert unsafe_non_session_routes(app) == exemptions | openclaw_machine_routes(app)
~~~

Also assert that every route under <code>/api/integrations/openclaw</code> is <code>authenticated_machine</code>, while <code>/api/system-settings/openclaw-integration</code> is <code>protected_browser</code>.

- [ ] **Step 2: Confirm RED against the current app**

~~~bash
cd backend
.venv/bin/python -m pytest tests/test_route_auth_inventory.py -q
~~~

Expected: many routes have no marker and trusted Plan 09 routes are absent in production.

- [ ] **Step 3: Build explicit parent routers**

~~~python
protected_browser_router = APIRouter(
    dependencies=[Depends(require_browser_route_policy)]
)
credential_exchange_router = APIRouter(
    dependencies=[Depends(require_credential_exchange_policy)]
)
setup_router = APIRouter(dependencies=[Depends(require_setup_policy)])
machine_router = APIRouter(
    dependencies=[Depends(require_authenticated_machine_policy)]
)
~~~

Attach a stable marker attribute to each dependency callable and have tests inspect FastAPI’s dependency graph; do not infer policy from path naming alone.

- [ ] **Step 4: Mount the complete router groups**

Protected browser routers include Entry, EntryType, Tag, Relation/RelationType, Attachment, AI Provider, AI Registry, AI generate, Assistant conversation/chat/stop/Interrupt, Assistant config Tool/Workflow/Agent, Skill/Profile, Eval/Admin, Stats, Graph, LightRAG, Report, protected system settings, and OpenClaw settings.

Public routes are limited to liveness, safe initialization status/defaults, and Plan 2’s later safe readiness route. Credential exchange contains login only. Setup contains initialization only. Machine contains OpenClaw runtime only.

Do not retain <code>mount_skill_admin_router(app_env=...)</code> or <code>mount_skill_eval_router(app_env=...)</code>.

- [ ] **Step 5: Stage a generic audit event atomically for unsafe browser requests**

<code>require_browser_route_policy</code> resolves Operator and CSRF, then adds a safe event to the same request Session before the endpoint runs:

~~~python
if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
    principal = require_operator_principal(...)
    require_csrf(...)
    OperatorAuditRepository(db).append(
        event_type="control_plane_mutation_committed",
        outcome="succeeded",
        context=context,
        operator_id=principal.operator_id,
        session_id=principal.session_id,
        metadata={
            "method": request.method,
            "routeName": request.scope["route"].name,
        },
    )
~~~

The event is pending in the same SQLAlchemy transaction. If an existing service commits, the mutation and event commit together. If it only flushes, the dependency’s successful teardown commits both. If the endpoint raises before commit, teardown rolls back both. Never store raw path parameters, query values, request body, Entry content, or headers.

- [ ] **Step 6: Make OpenClaw authentication a dependency**

Move <code>authorize_runtime_request()</code> out of handler bodies into <code>require_openclaw_machine_principal</code>. Its output remains <code>OpenClawRuntimeAuditContext</code>, not <code>OperatorPrincipal</code>. Tests prove:

- valid Bearer plus no session succeeds on machine endpoints;
- valid session plus no Bearer fails machine endpoints;
- valid Bearer fails browser settings endpoints;
- valid Operator session/CSRF succeeds on browser settings endpoints.

- [ ] **Step 7: Prove audit atomicity and full inventory**

Add a test that injects a PostgreSQL audit INSERT failure and asserts the domain row does not commit. Add the inverse test that a service exception leaves no generic success event.

~~~bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_route_auth_inventory.py \
  backend/tests/test_operator_auth_api.py \
  backend/tests/test_openclaw_integration.py -q
~~~

Expected: all application routes have one marker; machine and Operator authority remain disjoint.

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/main.py backend/app/operator_auth/route_policy.py backend/app/system_settings/router.py backend/app/openclaw_integration/router.py backend/tests/test_route_auth_inventory.py backend/tests/test_operator_auth_api.py backend/tests/test_openclaw_integration.py
git commit -m "feat(auth): protect every browser route by policy"
~~~

---

### Task 8: Remove trusted Header and CLI identity construction

**Files:**

- Modify: <code>backend/app/assistant/skills/principal.py</code>
- Modify: <code>backend/app/assistant/skills/admin_router.py</code>
- Modify: <code>backend/app/assistant/evaluation/router.py</code>
- Modify: <code>backend/app/assistant/skills/router.py</code>
- Modify: <code>backend/app/assistant/capability_calls/cli.py</code>
- Modify: <code>backend/app/config.py</code>
- Create: <code>backend/tests/test_operator_cli_boundary.py</code>
- Modify: trusted-header Skill/Eval API tests to use authenticated client fixtures.

**Interfaces:**

- Consumes: canonical <code>OperatorPrincipal</code> and protected parent router.
- Produces: one Principal type across services; no asserted identity mutation path.

- [ ] **Step 1: Write failing constructor and CLI tests**

~~~python
def test_no_production_module_reads_operator_identity_headers() -> None:
    sources = production_python_sources()
    forbidden = (
        "X-MindAtlas-Operator-Id",
        "X-MindAtlas-Operator-Role",
        "get_trusted_operator_principal",
    )
    assert not find_literals(sources, forbidden)


def test_reconciliation_decide_cannot_use_env_operator_id(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_CAPABILITY_RECONCILIATION_OPERATOR_ID", str(uuid4()))
    result = run_reconciliation_cli(["decide", "--call-id", str(uuid4())])
    assert result.exit_code == 2
    assert "authenticated HTTP Operator session is required" in result.stderr


def test_compatibility_principal_is_canonical_dataclass() -> None:
    from app.assistant.skills.principal import OperatorPrincipal as Compat
    from app.operator_auth.contracts import OperatorPrincipal
    assert Compat is OperatorPrincipal
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
cd backend
.venv/bin/python -m pytest \
  tests/test_operator_cli_boundary.py \
  tests/test_skill_admin_api.py \
  tests/test_skill_eval_api.py -q
~~~

Expected: source scan finds Header construction and CLI mutation remains env-authorized.

- [ ] **Step 3: Replace the old Pydantic Principal with a re-export**

~~~python
from app.operator_auth.contracts import OperatorPrincipal, OperatorRole

__all__ = ["OperatorPrincipal", "OperatorRole"]
~~~

Compatibility properties preserve <code>principal_id</code>, <code>is_operator</code>, and <code>audit_actor()</code>, so service changes remain mechanical and UUID-backed.

- [ ] **Step 4: Delete trusted mount and Header parsing**

Remove <code>_parse_trusted_principal</code>, <code>get_trusted_operator_principal</code>, conditional mount functions, and environment guards. Admin/Eval endpoints that directly consume a Principal use:

~~~python
principal: OperatorPrincipal = Depends(require_viewer_principal)
~~~

Operator mutations use <code>require_operator_principal</code>; parent policy still supplies CSRF. Replace direct <code>principal.principal_id</code> strings with <code>principal.audit_actor()</code> where a bounded actor string is persisted.

- [ ] **Step 5: Disable asserted-identity reconciliation mutation**

Keep CLI inspection read-only. The <code>decide</code> subcommand exits with code 2 and directs the user to the authenticated HTTP control plane. Remove <code>assistant_capability_reconciliation_operator_id</code> as an authorization source. Plan 4 supplies the final Operator+CSRF reconciliation endpoint and signed-evidence gate.

No password flag, password environment variable, role flag, Operator ID flag, or local-loopback shortcut is introduced.

- [ ] **Step 6: Convert API tests to real sessions**

Create a shared helper that initializes or seeds the singleton account, calls login with same-origin headers, copies the CSRF cookie into <code>X-MindAtlas-CSRF</code>, and returns the client. Replace every trusted-header fixture in Skill Admin, Eval, SSE, import preview, two-gate lifecycle, and Plan 09 end-to-end tests.

- [ ] **Step 7: Run focused source and API gates**

~~~bash
cd backend
.venv/bin/python -m pytest \
  tests/test_operator_cli_boundary.py \
  tests/test_skill_admin_api.py \
  tests/test_skill_eval_api.py \
  tests/test_skill_eval_sse.py \
  tests/test_plan09_lifecycle_e2e.py \
  tests/test_skill_two_gate_lifecycle.py \
  tests/test_agent_skill_import_preview.py -q
rg -n 'X-MindAtlas-Operator-(Id|Role)|VITE_MINDATLAS_OPERATOR' app tests ../frontend/src
~~~

Expected: tests pass; the source scan prints only explicit forged-header negative-test literals, never production code.

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/assistant/skills backend/app/assistant/evaluation/router.py backend/app/assistant/capability_calls/cli.py backend/app/config.py backend/tests
git commit -m "fix(auth): remove asserted operator identities"
~~~

---

### Task 9: Make session handling and CSRF universal in the frontend API client

**Files:**

- Modify: <code>frontend/src/lib/api/client.ts</code>
- Create: <code>frontend/src/features/operator-auth/api/operatorAuth.ts</code>
- Create: <code>frontend/src/features/operator-auth/api/operatorAuth.test.ts</code>
- Modify: <code>frontend/src/features/assistant-config/api/skill-packages.ts</code>
- Modify: <code>frontend/src/features/assistant-config/api/main-agent-profiles.ts</code>
- Modify: <code>frontend/src/features/assistant-config/api/skill-packages.test.ts</code>

**Interfaces:**

- Consumes: fixed cookies/header and auth HTTP contract.
- Produces: same-origin credential requests, automatic CSRF for unsafe methods, <code>SESSION_EXPIRED_EVENT</code>, and typed auth calls with no asserted Header authority.

- [ ] **Step 1: Write failing API client tests**

~~~typescript
it('sends cookies and csrf on unsafe requests', async () => {
  document.cookie = 'mindatlas_csrf=csrf-value; Path=/'
  const fetcher = vi.fn().mockResolvedValue(ok({ changed: true }))
  const client = new ApiClient({ fetcher: fetcher as typeof fetch })
  await client.put('/api/system-settings/locale', { body: { locale: 'en' } })
  const init = fetcher.mock.calls[0][1] as RequestInit
  expect(init.credentials).toBe('same-origin')
  expect(new Headers(init.headers).get('X-MindAtlas-CSRF')).toBe('csrf-value')
})

it('emits one session-expired event for protected 401', async () => {
  const listener = vi.fn()
  window.addEventListener(SESSION_EXPIRED_EVENT, listener)
  const client = new ApiClient({ fetcher: vi.fn().mockResolvedValue(failed(401)) })
  await expect(client.get('/api/entries')).rejects.toMatchObject({ status: 401 })
  expect(listener).toHaveBeenCalledTimes(1)
})
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
cd frontend
npm test -- src/features/operator-auth/api/operatorAuth.test.ts
~~~

Expected: test file or session-aware client behavior is absent.

- [ ] **Step 3: Add credentials and CSRF injection**

~~~typescript
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function readCookie(name: string): string | undefined {
  const prefix = name + '='
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length)
}

const init: RequestInit = {
  method: options.method,
  headers,
  signal: options.signal,
  credentials: 'same-origin',
}
if (!SAFE_METHODS.has(options.method) && !headers.has('X-MindAtlas-CSRF')) {
  const csrf = readCookie('mindatlas_csrf')
  if (csrf) headers.set('X-MindAtlas-CSRF', decodeURIComponent(csrf))
}
~~~

Do not attach CSRF to login or initialization when the cookie is absent. The backend’s same-origin policy protects those credential exchanges.

- [ ] **Step 4: Add centralized session-expired signaling**

~~~typescript
export const SESSION_EXPIRED_EVENT = 'mindatlas:session-expired'

function reportSessionExpired(path: string, status: number): void {
  if (
    status === 401 &&
    path !== '/api/operator-auth/login' &&
    path !== '/api/system-settings/initialize'
  ) {
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
  }
}
~~~

Call this once before throwing <code>ApiError</code>. Never log response bodies or cookies.

- [ ] **Step 5: Add typed auth client**

~~~typescript
export interface OperatorSession {
  authenticated: boolean
  role?: 'operator'
  idleExpiresAt?: string
  absoluteExpiresAt?: string
}

export function getOperatorSession(): Promise<OperatorSession> {
  return apiClient.get<OperatorSession>('/api/operator-auth/session')
}

export function loginOperator(password: string): Promise<OperatorSession> {
  return apiClient.post<OperatorSession>('/api/operator-auth/login', {
    body: { password },
  })
}

export function logoutOperator(): Promise<{ loggedOut: true }> {
  return apiClient.post('/api/operator-auth/logout', { body: {} })
}
~~~

- [ ] **Step 6: Delete all Vite/operator Header construction**

Remove <code>readViteEnv()</code>, <code>skillAdminOperatorHeaders()</code>, imports, and per-call Header options. Update tests to assert no <code>X-MindAtlas-Operator-*</code> header is generated.

- [ ] **Step 7: Run focused tests and commit**

~~~bash
cd frontend
npm test -- \
  src/features/operator-auth/api/operatorAuth.test.ts \
  src/features/assistant-config/api/skill-packages.test.ts
cd ..
git add frontend/src/lib/api/client.ts frontend/src/features/operator-auth/api frontend/src/features/assistant-config/api
git commit -m "feat(auth): make browser API session aware"
~~~

Expected: focused Vitest files pass.

---

### Task 10: Add initialization password/setup inputs, login page, and Operator route gate

**Files:**

- Create: <code>frontend/src/features/operator-auth/queries.ts</code>
- Create: <code>frontend/src/features/operator-auth/components/OperatorGate.tsx</code>
- Create: <code>frontend/src/features/operator-auth/components/OperatorGate.test.tsx</code>
- Create: <code>frontend/src/features/operator-auth/pages/OperatorLoginPage.tsx</code>
- Create: <code>frontend/src/features/operator-auth/pages/OperatorLoginPage.test.tsx</code>
- Create: <code>frontend/src/features/operator-auth/index.ts</code>
- Modify: <code>frontend/src/features/initialization/api/systemInitialization.ts</code>
- Modify: <code>frontend/src/features/initialization/pages/SystemInitializationPage.tsx</code>
- Modify: <code>frontend/src/features/initialization/components/InitializationGate.tsx</code>
- Modify: <code>frontend/src/features/initialization/components/InitializationGate.test.tsx</code>
- Modify: <code>frontend/src/app/App.tsx</code>
- Modify: locale resource files under <code>frontend/src/locales/</code>

**Interfaces:**

- Consumes: Task 9 API and session-expired event.
- Produces: in-memory setup exchange, exact password login, initialized/authenticated route state, and post-expiry redirect.

- [ ] **Step 1: Write failing gate and exact-input tests**

~~~typescript
it('sends setup token only in Authorization and never persists it', async () => {
  await initializeSystem(validPayload, 'one-time-setup-token-with-32-bytes')
  const init = fetcher.mock.calls[0][1] as RequestInit
  expect(new Headers(init.headers).get('Authorization')).toBe(
    'Setup one-time-setup-token-with-32-bytes',
  )
  expect(String(init.body)).not.toContain('one-time-setup-token-with-32-bytes')
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

it('redirects initialized unauthenticated users to login', async () => {
  renderApp({ initialized: true, authenticated: false, path: '/dashboard' })
  expect(await screen.findByRole('heading', { name: /operator login/i })).toBeVisible()
})

it('does not trim the operator password', async () => {
  renderLogin()
  await user.type(screen.getByLabelText(/password/i), '  exact password  ')
  await user.click(screen.getByRole('button', { name: /login/i }))
  expect(loginOperator).toHaveBeenCalledWith('  exact password  ')
})
~~~

- [ ] **Step 2: Confirm RED**

~~~bash
cd frontend
npm test -- \
  src/features/operator-auth/components/OperatorGate.test.tsx \
  src/features/operator-auth/pages/OperatorLoginPage.test.tsx \
  src/features/initialization/components/InitializationGate.test.tsx
~~~

Expected: auth components do not exist and initialization has no credentials.

- [ ] **Step 3: Extend initialization without persisting secrets**

The request separates the Setup Token from JSON:

~~~typescript
export function initializeSystem(
  payload: InitializeSystemRequest,
  setupToken: string,
): Promise<InitializationCompletionResponse> {
  return apiClient.post('/api/system-settings/initialize', {
    body: payload,
    headers: { Authorization: 'Setup ' + setupToken },
  })
}
~~~

<code>InitializeSystemRequest</code> adds <code>operatorPassword</code>. The page keeps password and Setup Token only in component state, never Zustand, Query cache, URL state, local/session storage, analytics, or toast details. Clear both values in <code>finally</code>.

- [ ] **Step 4: Implement session query and gate**

Use a non-retrying query for 401:

~~~typescript
export function useOperatorSessionQuery(enabled = true) {
  return useQuery({
    queryKey: ['operator-session'],
    queryFn: getOperatorSession,
    enabled,
    retry: false,
    staleTime: 30_000,
  })
}
~~~

<code>InitializationGate</code> remains outermost and bypasses <code>OperatorGate</code> while the system is uninitialized or the location is <code>/initialize</code>. When initialized, <code>OperatorGate</code> allows <code>/login</code>, checks the session, routes unauthenticated users to <code>/login</code>, and routes authenticated users away from <code>/login</code>. It listens for <code>SESSION_EXPIRED_EVENT</code>, clears protected Query caches, invalidates session state, and shows one generic expiry notification.

- [ ] **Step 5: Implement login UI and route**

The page has one password field, no username, no remember-me option, no role selector, and generic invalid/locked/auth-unavailable messages. Do not trim or normalize before <code>loginOperator()</code>.

Mount:

~~~tsx
<InitializationGate>
  <OperatorGate>
    <Routes>
      <Route path="/login" element={withPageFallback(<OperatorLoginPage />)} />
      ...
    </Routes>
  </OperatorGate>
</InitializationGate>
~~~

The initialization route must remain accessible before authentication and, on success, use the session cookies returned by setup.

- [ ] **Step 6: Cover session expiry and failed initialization cleanup**

Tests cover loading, status failure, clean uninitialized redirect, initialization success with immediate authenticated navigation, setup failure with cleared secrets, login success, lockout copy, session expiry while on a protected page, and no redirect loop.

- [ ] **Step 7: Run frontend tests/build and commit**

~~~bash
cd frontend
npm test -- \
  src/features/operator-auth \
  src/features/initialization/components/InitializationGate.test.tsx
npm run build
cd ..
git add frontend/src/app/App.tsx frontend/src/features/operator-auth frontend/src/features/initialization frontend/src/locales
git commit -m "feat(auth): add operator setup and login gates"
~~~

Expected: focused tests pass and the production TypeScript/Vite build succeeds.

---

### Task 11: Production configuration, restart/rotation rehearsal, and sanitized evidence

**Files:**

- Create: <code>backend/scripts/verify_operator_control_plane.py</code>
- Modify: <code>deploy/docker-compose.yml</code>
- Modify: <code>deploy/README.md</code>
- Modify: <code>backend/.env.example</code>
- Modify: <code>.github/workflows/ci.yml</code>
- Create during verification: <code>docs/superpowers/evidence/2026-07-28-operator-control-plane-verification.json</code>

**Interfaces:**

- Consumes: all previous Plan 1 tasks.
- Produces: production-safe environment contract, CI gates, restart/rotation proof, and sanitized machine-readable evidence.

- [ ] **Step 1: Add failing evidence schema tests**

The runner output allows only:

~~~python
ALLOWED_EVIDENCE_KEYS = {
    "schemaVersion",
    "buildRevision",
    "alembicHead",
    "postgresVersion",
    "routePolicyCounts",
    "testSuites",
    "restartSessionPreserved",
    "rotationSucceeded",
    "previousKeySessionsRevoked",
    "generatedAtUtc",
    "aggregateDigest",
}


def test_evidence_contains_no_sensitive_keys(evidence: dict[str, object]) -> None:
    assert set(evidence) == ALLOWED_EVIDENCE_KEYS
    serialized = json.dumps(evidence).lower()
    for fragment in ("password", "token", "cookie", "api_key", "prompt", "entry_content"):
        assert fragment not in serialized
~~~

- [ ] **Step 2: Wire production configuration without defaults**

Compose accepts secret-store/file injection for:

- <code>MINDATLAS_INITIAL_SETUP_TOKEN</code>;
- <code>MINDATLAS_CANONICAL_ORIGIN</code>;
- <code>MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID</code>;
- <code>MINDATLAS_SESSION_HMAC_KEYS</code>.

The example shows generation commands that print to an operator-controlled terminal but contains no real or reusable value:

~~~bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
python3 -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
~~~

Document HTTPS termination, exact Origin/CORS agreement, backup of the key ring, active-plus-previous rotation, revocation before previous-key removal, and recovery via normal login after failed initial-session issuance.

- [ ] **Step 3: Add fixed verification runner**

The runner invokes fixed commands rather than accepting arbitrary test names:

~~~python
SUITES = (
    ("password_config", ["tests/test_operator_password.py", "tests/test_operator_auth_config.py"]),
    ("service_api", ["tests/test_operator_auth_service.py", "tests/test_operator_auth_api.py"]),
    ("postgres", ["tests/test_operator_auth_postgres.py"]),
    ("route_inventory", ["tests/test_route_auth_inventory.py", "tests/test_operator_cli_boundary.py"]),
)
~~~

It runs each suite, records only counts/pass state and tool versions, performs a real API restart with the same key, rotates active/previous keys, removes the previous key after durable revocation, validates the JSON allowlist, computes SHA-256 over canonical JSON excluding <code>aggregateDigest</code>, and writes atomically.

- [ ] **Step 4: Add CI gates**

CI provisions PostgreSQL, sets synthetic test-only secrets through job environment, upgrades to <code>9f3c1a7e2b40</code>, and runs auth/inventory tests. No release-critical test uses <code>pytest.skip</code> when PostgreSQL is absent; the job fails.

- [ ] **Step 5: Execute the production-shaped auth rehearsal**

~~~bash
cd backend
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python scripts/verify_operator_control_plane.py \
  --output ../docs/superpowers/evidence/2026-07-28-operator-control-plane-verification.json
~~~

Expected: exit 0 and one JSON artifact whose <code>aggregateDigest</code> is 64 lowercase hex characters. Inspect it manually for the safe-key allowlist before commit.

- [ ] **Step 6: Run final Plan 1 verification**

~~~bash
cd backend
.venv/bin/python -m pytest \
  tests/test_operator_password.py \
  tests/test_operator_auth_config.py \
  tests/test_operator_auth_service.py \
  tests/test_operator_auth_api.py \
  tests/test_operator_auth_postgres.py \
  tests/test_route_auth_inventory.py \
  tests/test_operator_cli_boundary.py \
  tests/test_system_initialization_service.py \
  tests/test_openclaw_integration.py -q
.venv/bin/python -m pytest -q
cd ../frontend
npm test
npm run build
cd ..
git diff --check
~~~

Expected: focused backend, full backend, full frontend, and production build pass; <code>git diff --check</code> prints nothing.

- [ ] **Step 7: Commit**

~~~bash
git add backend/.env.example backend/scripts/verify_operator_control_plane.py deploy/docker-compose.yml deploy/README.md .github/workflows/ci.yml docs/superpowers/evidence/2026-07-28-operator-control-plane-verification.json
git commit -m "test(auth): verify production operator boundary"
~~~

---

## Plan 1 Exit Gate

Run from a fresh checkout with Python 3.11, Node/npm matching CI, and disposable PostgreSQL:

~~~bash
git status --short
cd backend
python3.11 -m venv .venv-plan1
.venv-plan1/bin/python -m pip install --upgrade pip
.venv-plan1/bin/python -m pip install -r requirements.txt pytest
DATABASE_URL="$MINDATLAS_TEST_POSTGRES_URL" .venv-plan1/bin/alembic upgrade 9f3c1a7e2b40
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv-plan1/bin/python -m pytest -q
cd ../frontend
npm ci
npm test
npm run build
cd ..
git diff --check
~~~

Expected:

- exactly one enabled singleton Operator can exist;
- concurrent initialization yields one 200 and one 409;
- invalid Setup authorization is 401 before body-domain validation;
- passwords preserve every submitted character and meet the exact Argon2id policy;
- lockout, expiry, restart, rotation, revocation, and password revision tests pass;
- session cookie is HttpOnly and CSRF cookie/header pairing is enforced;
- every application route has exactly one explicit policy;
- forged Header, environment, and CLI identities fail closed;
- OpenClaw Bearer remains independent;
- append-only audit UPDATE/DELETE fail in PostgreSQL;
- frontend initialization, login, and expiry behavior pass;
- no test required by this exit gate is skipped;
- evidence contains only allowlisted safe keys;
- <code>git diff --check</code> is clean.

## Rollback Boundary

- Before any environment initializes an Operator, code and additive revision <code>9f3c1a7e2b40</code> may be reverted only on a disposable uninitialized database using the guarded test downgrade.
- After an Operator account or session has existed in an environment, rolling back to unprotected mutations or Header/CLI authority is forbidden. Apply a forward security fix.
- Disabling new Assistant Runs is a Plan 2 runtime action and does not bypass Operator authentication.
- Loss of the active HMAC key does not authorize reset or a secretless login. Restore the backed-up key ring or follow an explicit forward recovery procedure that preserves audit.
- Plan 3 will archive this revision into the pre-GA clean baseline; it will not discard the three auth tables or their protections.

## Implementation Stop Conditions

Stop the plan and escalate with exact evidence if any of these occurs:

- the baseline is not clean or the planned revision ID collides;
- PostgreSQL cannot enforce singleton or append-only constraints;
- an unsafe route cannot be classified under one explicit policy;
- an existing machine endpoint would need an Operator session to retain functionality;
- an existing browser mutation cannot include its generic audit event atomically;
- a secret appears in a log, error, audit event, test artifact, or committed example;
- full tests reveal an authorization regression that cannot be resolved without weakening the fixed contract.

## Authoring Self-Review Record

- Spec coverage: Tasks 1–5 cover password, token, key-ring, account, session, lockout, cookie, Origin, CORS, CSRF, RBAC, revocation, and audit requirements; Task 6 covers setup authorization, clean-only initialization, concurrency, and transaction ownership; Tasks 7–8 cover exhaustive route ownership, OpenClaw separation, Header/CLI rejection, and production mounting; Tasks 9–10 cover browser credentials, initialization/login/session-expiry UX; Task 11 covers deployment, restart/rotation, CI, clean install, full regression, and safe evidence.
- Stable-interface check: <code>OperatorPrincipal</code>, <code>OperatorAuthAvailability</code>, <code>IssuedSession</code>, <code>OPERATOR_AUTH_CONTRACT_VERSION</code>, <code>CoreInitializationResult</code>, and <code>InitializationCoordinator</code> have one canonical spelling and explicit downstream consumers.
- Migration check: Plan 1 is exactly <code>3bd7bc4257c9 -&gt; 9f3c1a7e2b40</code>; no Plan 2, clean-root, or launch-gate revision is created here.
- Placeholder scan:

~~~bash
rg -n 'T[B]D|T[O]DO|F[I]XME|implement[[:space:]]+later|fill[[:space:]]+in|similar[[:space:]]+to[[:space:]]+Task' \
  docs/superpowers/plans/2026-07-28-single-operator-production-control-plane.md
~~~

Expected: no output.

- Formatting check:

~~~bash
git diff --check -- docs/superpowers/plans/2026-07-28-single-operator-production-control-plane.md
~~~

Expected: no output.
