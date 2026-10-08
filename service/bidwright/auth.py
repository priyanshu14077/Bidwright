"""Sign-up, sign-in, workspaces and members, plus the permission check every endpoint uses.

    @app.get("/api/things")
    def things(who: Principal = Depends(require("read"))): ...

require() resolves the session cookie, checks the role in the session's
workspace, and enters that workspace for the rest of the request (including
its background tasks). It is async on purpose: FastAPI runs sync dependencies
in a separate worker thread, and a context variable set there would never
reach the endpoint.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from bidwright import tenancy
from bidwright.config import settings
from bidwright.db import current_org, engine, enter
from bidwright.tenancy import Principal

COOKIE = "bidwright_session"

router = APIRouter(prefix="/api/auth")


def _resolve(token: str | None) -> Principal | None:
    if not token:
        return None
    with engine.begin() as conn:
        return tenancy.resolve(conn, token)


async def signed_in(bidwright_session: str | None = Cookie(None)) -> Principal:
    who = await run_in_threadpool(_resolve, bidwright_session)
    if who is None:
        raise HTTPException(401, "sign in first")
    return who


def require(permission: str):
    async def check(who: Principal = Depends(signed_in)) -> Principal:
        if who.org_id is None:
            raise HTTPException(403, "no workspace selected")
        if not who.can(permission):
            raise HTTPException(403, f"your role ({who.role}) cannot do this")
        current_org.set(str(who.org_id))
        return who
    return check


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, path="/")


# ---------------------------------------------------------------- sign-up and sign-in

class SignUp(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    workspace: str = Field(min_length=2, max_length=120)
    practice_description: str | None = Field(None, max_length=400)
    invitation: str | None = None


@router.post("/signup")
def signup(body: SignUp, response: Response):
    from bidwright.archive.loader import seed_workspace

    with engine.begin() as conn:
        try:
            user_id = tenancy.create_user(conn, body.email, body.name, body.password)
        except IntegrityError:
            raise HTTPException(409, "an account with this email already exists; sign in instead")
        if body.invitation:
            org_id = tenancy.accept_invitation(conn, body.invitation, user_id, body.email.lower())
            if org_id is None:
                raise HTTPException(400, "this invitation is not valid for this email, or has expired")
        else:
            org_id = tenancy.create_organization(conn, body.workspace, user_id, body.practice_description)
            enter(conn, org_id)
            seed_workspace(conn)
        token = tenancy.start_session(conn, user_id, org_id)
    set_cookie(response, token)
    return {"org_id": org_id}


class SignIn(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: SignIn, response: Response):
    with engine.begin() as conn:
        user_id = tenancy.authenticate(conn, body.email, body.password)
        if user_id is None:
            raise HTTPException(401, "email or password is wrong")
        token = tenancy.start_session(conn, user_id, tenancy.default_org(conn, user_id))
    set_cookie(response, token)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response, bidwright_session: str | None = Cookie(None)):
    if bidwright_session:
        with engine.begin() as conn:
            tenancy.end_session(conn, bidwright_session)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(who: Principal = Depends(signed_in)):
    with engine.connect() as conn:
        orgs = tenancy.memberships(conn, who.user_id)
    current = next((o for o in orgs if o["org_id"] == who.org_id), None)
    return {"user": {"user_id": who.user_id, "email": who.email, "name": who.name},
            "workspace": current, "role": who.role,
            "permissions": sorted(tenancy.PERMISSIONS.get(who.role, set())) if who.role else [],
            "workspaces": orgs}


class Switch(BaseModel):
    org_id: UUID


@router.post("/switch")
def switch(body: Switch, who: Principal = Depends(signed_in), bidwright_session: str | None = Cookie(None)):
    with engine.begin() as conn:
        if not tenancy.switch(conn, bidwright_session, who.user_id, body.org_id):
            raise HTTPException(403, "you are not a member of that workspace")
    return {"ok": True}


class NewWorkspace(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    practice_description: str | None = Field(None, max_length=400)


@router.post("/workspaces")
def new_workspace(body: NewWorkspace, who: Principal = Depends(signed_in),
                  bidwright_session: str | None = Cookie(None)):
    from bidwright.archive.loader import seed_workspace

    with engine.begin() as conn:
        org_id = tenancy.create_organization(conn, body.name, who.user_id, body.practice_description)
        enter(conn, org_id)
        seed_workspace(conn)
        tenancy.switch(conn, bidwright_session, who.user_id, org_id)
    return {"org_id": org_id}


# ---------------------------------------------------------------- members and invitations

@router.get("/members")
def members(who: Principal = Depends(require("read"))):
    with engine.connect() as conn:
        people = [dict(r._mapping) for r in conn.execute(text("""
            SELECT u.user_id, u.email, u.name, m.role, m.created_at AS joined_at, u.last_login_at
            FROM tenancy.membership m JOIN tenancy.app_user u USING (user_id)
            WHERE m.org_id = :o ORDER BY m.created_at"""), dict(o=who.org_id))]
        invites = [dict(r._mapping) for r in conn.execute(text("""
            SELECT email, role, created_at, expires_at FROM tenancy.invitation
            WHERE org_id = :o AND accepted_at IS NULL AND expires_at > now() ORDER BY created_at DESC"""),
            dict(o=who.org_id))] if who.can("members") else []
    return {"members": people, "invitations": invites}


class Invite(BaseModel):
    email: EmailStr
    role: Literal["admin", "estimator", "viewer"]


@router.post("/invitations")
def invite(body: Invite, who: Principal = Depends(require("members"))):
    if body.role == "admin" and who.role != "owner":
        raise HTTPException(403, "only an owner can invite an admin")
    with engine.begin() as conn:
        token = tenancy.create_invitation(conn, who.org_id, body.email, body.role, who.user_id)
    # No email delivery yet: the inviter copies the link.
    return {"link": f"/app/#/join/{token}", "email": body.email.lower(), "role": body.role}


class Join(BaseModel):
    token: str


@router.post("/join")
def join(body: Join, who: Principal = Depends(signed_in), bidwright_session: str | None = Cookie(None)):
    with engine.begin() as conn:
        org_id = tenancy.accept_invitation(conn, body.token, who.user_id, who.email)
        if org_id is None:
            raise HTTPException(400, f"this invitation is not for {who.email}, or has expired")
        tenancy.switch(conn, bidwright_session, who.user_id, org_id)
    return {"org_id": org_id}


class RoleChange(BaseModel):
    role: Literal["owner", "admin", "estimator", "viewer"]


def _guard_change(conn, who: Principal, user_id: UUID, new_role: str | None) -> str:
    target = conn.execute(text("SELECT role FROM tenancy.membership WHERE org_id = :o AND user_id = :u FOR UPDATE"),
                          dict(o=who.org_id, u=user_id)).scalar()
    if target is None:
        raise HTTPException(404, "not a member of this workspace")
    if who.role != "owner" and ("owner" in (target, new_role) or "admin" in (target, new_role)):
        raise HTTPException(403, "only an owner can change owners or admins")
    if target == "owner" and new_role != "owner":
        owners = conn.execute(text("SELECT count(*) FROM tenancy.membership WHERE org_id = :o AND role = 'owner'"),
                              dict(o=who.org_id)).scalar()
        if owners <= 1:
            raise HTTPException(409, "a workspace needs at least one owner")
    return target


@router.put("/members/{user_id}")
def change_role(user_id: UUID, body: RoleChange, who: Principal = Depends(require("members"))):
    with engine.begin() as conn:
        _guard_change(conn, who, user_id, body.role)
        conn.execute(text("UPDATE tenancy.membership SET role = :r WHERE org_id = :o AND user_id = :u"),
                     dict(r=body.role, o=who.org_id, u=user_id))
    return {"ok": True}


@router.delete("/members/{user_id}")
def remove_member(user_id: UUID, who: Principal = Depends(require("members"))):
    with engine.begin() as conn:
        _guard_change(conn, who, user_id, None)
        conn.execute(text("DELETE FROM tenancy.membership WHERE org_id = :o AND user_id = :u"),
                     dict(o=who.org_id, u=user_id))
    return {"ok": True}
