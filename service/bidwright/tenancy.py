"""Workspaces, users, roles and sessions.

The tenancy tables have no row-level security: this module is the only reader,
and every query here filters by user, workspace or token explicitly.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import Connection, text

from bidwright.config import settings

ROLES = ("owner", "admin", "estimator", "viewer")

# What each role can do. Endpoints ask for one permission; a role either has it or not.
PERMISSIONS = {
    "viewer": {"read"},
    "estimator": {"read", "intake", "review"},
    "admin": {"read", "intake", "review", "reference", "backtest", "members"},
    "owner": {"read", "intake", "review", "reference", "backtest", "members", "workspace"},
}

_hasher = PasswordHasher()


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    name: str
    org_id: UUID | None
    role: str | None

    def can(self, permission: str) -> bool:
        return self.role is not None and permission in PERMISSIONS[self.role]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "workspace"
    return slug if len(slug) >= 3 else f"{slug}-ws"


def unique_slug(conn: Connection, name: str) -> str:
    base = slugify(name)
    taken = set(conn.execute(text("SELECT slug FROM tenancy.organization WHERE slug LIKE :b || '%'"),
                             dict(b=base)).scalars())
    if base not in taken:
        return base
    return next(f"{base}-{n}" for n in range(2, 10_000) if f"{base}-{n}" not in taken)


def create_user(conn: Connection, email: str, name: str, password: str) -> UUID:
    return conn.execute(
        text("INSERT INTO tenancy.app_user (email, name, password_hash) VALUES (:e, :n, :h) RETURNING user_id"),
        dict(e=email.strip().lower(), n=name.strip(), h=hash_password(password))).scalar_one()


def create_organization(conn: Connection, name: str, owner_id: UUID, practice_description: str | None = None,
                        slug: str | None = None, is_demo: bool = False) -> UUID:
    org_id = conn.execute(
        text("""INSERT INTO tenancy.organization (slug, name, practice_description, is_demo)
                VALUES (:s, :n, COALESCE(:d, 'an architecture and design practice'), :demo) RETURNING org_id"""),
        dict(s=slug or unique_slug(conn, name), n=name.strip(), d=practice_description, demo=is_demo)).scalar_one()
    add_member(conn, org_id, owner_id, "owner")
    return org_id


def add_member(conn: Connection, org_id: UUID, user_id: UUID, role: str) -> None:
    conn.execute(text("""INSERT INTO tenancy.membership (org_id, user_id, role) VALUES (:o, :u, :r)
                         ON CONFLICT (org_id, user_id) DO UPDATE SET role = EXCLUDED.role"""),
                 dict(o=org_id, u=user_id, r=role))


def ensure_demo(conn: Connection, org: dict, user: dict) -> UUID:
    """The demo workspace and its sign-in. Idempotent."""
    user_id = conn.execute(text("SELECT user_id FROM tenancy.app_user WHERE email = :e"),
                           dict(e=user["email"])).scalar()
    if user_id is None:
        user_id = create_user(conn, user["email"], user["name"], user["password"])
    org_id = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE slug = :s"), dict(s=org["slug"])).scalar()
    if org_id is None:
        org_id = create_organization(conn, org["name"], user_id, org["practice_description"], org["slug"], is_demo=True)
    else:
        conn.execute(text("UPDATE tenancy.organization SET name = :n, practice_description = :d WHERE org_id = :o"),
                     dict(n=org["name"], d=org["practice_description"], o=org_id))
    add_member(conn, org_id, user_id, "owner")
    return org_id


def authenticate(conn: Connection, email: str, password: str) -> UUID | None:
    row = conn.execute(text("SELECT user_id, password_hash FROM tenancy.app_user WHERE email = :e"),
                       dict(e=email.strip().lower())).first()
    if row is None:
        verify_password(_DUMMY_HASH, password)  # same work whether or not the email exists
        return None
    if not verify_password(row.password_hash, password):
        return None
    conn.execute(text("UPDATE tenancy.app_user SET last_login_at = now() WHERE user_id = :u"), dict(u=row.user_id))
    return row.user_id


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def default_org(conn: Connection, user_id: UUID) -> UUID | None:
    """The workspace a user lands in: their oldest membership."""
    return conn.execute(text("SELECT org_id FROM tenancy.membership WHERE user_id = :u ORDER BY created_at LIMIT 1"),
                        dict(u=user_id)).scalar()


def start_session(conn: Connection, user_id: UUID, org_id: UUID | None) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(text("INSERT INTO tenancy.session (token_hash, user_id, org_id, expires_at) VALUES (:t, :u, :o, :x)"),
                 dict(t=token_hash(token), u=user_id, o=org_id,
                      x=datetime.now(timezone.utc) + timedelta(days=settings.session_days)))
    return token


def end_session(conn: Connection, token: str) -> None:
    conn.execute(text("DELETE FROM tenancy.session WHERE token_hash = :t"), dict(t=token_hash(token)))


def resolve(conn: Connection, token: str) -> Principal | None:
    """The signed-in user and their role in the session's workspace. A lost membership leaves no workspace."""
    row = conn.execute(text("""
        SELECT u.user_id, u.email, u.name, m.org_id, m.role
        FROM tenancy.session s JOIN tenancy.app_user u USING (user_id)
        LEFT JOIN tenancy.membership m ON m.user_id = s.user_id AND m.org_id = s.org_id
        WHERE s.token_hash = :t AND s.expires_at > now()"""), dict(t=token_hash(token))).first()
    return Principal(row.user_id, row.email, row.name, row.org_id, row.role) if row else None


def switch(conn: Connection, token: str, user_id: UUID, org_id: UUID) -> bool:
    member = conn.execute(text("SELECT 1 FROM tenancy.membership WHERE user_id = :u AND org_id = :o"),
                          dict(u=user_id, o=org_id)).first()
    if member:
        conn.execute(text("UPDATE tenancy.session SET org_id = :o WHERE token_hash = :t"),
                     dict(o=org_id, t=token_hash(token)))
    return member is not None


def memberships(conn: Connection, user_id: UUID) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text("""
        SELECT o.org_id, o.slug, o.name, o.is_demo, m.role FROM tenancy.membership m
        JOIN tenancy.organization o USING (org_id) WHERE m.user_id = :u ORDER BY m.created_at"""), dict(u=user_id))]


def create_invitation(conn: Connection, org_id: UUID, email: str, role: str, invited_by: UUID) -> str:
    token = secrets.token_urlsafe(24)
    conn.execute(text("""INSERT INTO tenancy.invitation (token_hash, org_id, email, role, invited_by, expires_at)
                         VALUES (:t, :o, :e, :r, :by, now() + interval '7 days')"""),
                 dict(t=token_hash(token), o=org_id, e=email.strip().lower(), r=role, by=invited_by))
    return token


def accept_invitation(conn: Connection, token: str, user_id: UUID, email: str) -> UUID | None:
    """Joins the user to the invitation's workspace if it is open and addressed to them."""
    inv = conn.execute(text("""SELECT org_id, role FROM tenancy.invitation WHERE token_hash = :t AND email = :e
                               AND accepted_at IS NULL AND expires_at > now() FOR UPDATE"""),
                       dict(t=token_hash(token), e=email)).first()
    if inv is None:
        return None
    exists = conn.execute(text("SELECT 1 FROM tenancy.membership WHERE org_id = :o AND user_id = :u"),
                          dict(o=inv.org_id, u=user_id)).first()
    if not exists:
        add_member(conn, inv.org_id, user_id, inv.role)
    conn.execute(text("UPDATE tenancy.invitation SET accepted_at = now() WHERE token_hash = :t"),
                 dict(t=token_hash(token)))
    return inv.org_id
