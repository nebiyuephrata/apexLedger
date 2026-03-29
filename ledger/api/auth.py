from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Header, HTTPException, Request, status
from jwt import PyJWKClient


@dataclass
class AuthContext:
    user_id: str
    role: str
    org_id: str | None
    email: str | None = None
    display_name: str | None = None
    is_internal: bool = False
    identity_type: str = "human"
    permissions: set[str] = field(default_factory=set)
    auth_source: str = "dev"


class ClerkJWTVerifier:
    def __init__(self) -> None:
        self.issuer = os.environ.get("CLERK_JWT_ISSUER", "").rstrip("/")
        self.jwks_url = os.environ.get("CLERK_JWKS_URL") or (f"{self.issuer}/.well-known/jwks.json" if self.issuer else "")
        self.audience = os.environ.get("CLERK_JWT_AUDIENCE")
        self._client = PyJWKClient(self.jwks_url) if self.jwks_url else None

    def verify(self, token: str) -> dict[str, Any]:
        if not self._client:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clerk verifier is not configured")
        signing_key = self._client.get_signing_key_from_jwt(token)
        kwargs: dict[str, Any] = {
            "algorithms": ["RS256"],
            "issuer": self.issuer or None,
            "options": {"verify_aud": bool(self.audience)},
        }
        if self.audience:
            kwargs["audience"] = self.audience
        return jwt.decode(token, signing_key.key, **kwargs)


_verifier = ClerkJWTVerifier()


def _boolish(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


async def resolve_auth_context(
    request: Request,
    authorization: str | None = Header(default=None),
    x_ledger_dev_role: str | None = Header(default=None),
    x_ledger_dev_org_id: str | None = Header(default=None),
    x_ledger_dev_user_id: str | None = Header(default=None),
    x_ledger_dev_internal: str | None = Header(default=None),
    x_ledger_dev_email: str | None = Header(default=None),
    x_ledger_dev_name: str | None = Header(default=None),
) -> AuthContext:
    allow_dev = _boolish(os.environ.get("LEDGER_ALLOW_DEV_AUTH"), default=False)
    if allow_dev and x_ledger_dev_role:
        return AuthContext(
            user_id=x_ledger_dev_user_id or f"dev-{x_ledger_dev_role}",
            role=x_ledger_dev_role,
            org_id=x_ledger_dev_org_id or "org_demo",
            email=x_ledger_dev_email,
            display_name=x_ledger_dev_name or x_ledger_dev_role.replace("_", " ").title(),
            is_internal=_boolish(x_ledger_dev_internal, default=x_ledger_dev_role in {"admin", "security_officer", "auditor"}),
            identity_type="service" if _boolish(x_ledger_dev_internal, default=False) else "human",
            auth_source="dev",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = _verifier.verify(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Clerk token: {exc}") from exc

    public_meta = claims.get("public_metadata") or {}
    unsafe_meta = claims.get("unsafe_metadata") or {}
    if not _verifier.issuer or not _verifier.jwks_url:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clerk verifier is not configured for production use")
    role = (
        claims.get("role")
        or claims.get("org_role")
        or public_meta.get("role")
        or unsafe_meta.get("role")
        or "loan_officer"
    )
    org_id = claims.get("org_id") or claims.get("organization_id") or public_meta.get("org_id") or unsafe_meta.get("org_id")
    org_membership = claims.get("org_membership") or public_meta.get("org_membership") or unsafe_meta.get("org_membership")
    perms = claims.get("permissions") or public_meta.get("permissions") or unsafe_meta.get("permissions") or []
    if isinstance(perms, str):
        perms = [perms]
    permissions = {str(p) for p in perms}
    identity_type = str(
        claims.get("actor_type")
        or public_meta.get("actor_type")
        or unsafe_meta.get("actor_type")
        or ("service" if "svc:browser_api" in permissions or "svc:internal_automation" in permissions else "human")
    )
    if role != "admin" and not org_id and identity_type != "service":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user is missing organization membership")
    if org_membership is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Authenticated user is not an active member of the requested organization")

    return AuthContext(
        user_id=str(claims.get("sub") or claims.get("user_id") or "unknown"),
        role=str(role),
        org_id=str(org_id) if org_id else None,
        email=claims.get("email") or public_meta.get("email"),
        display_name=claims.get("name") or claims.get("preferred_username"),
        is_internal=identity_type == "service",
        identity_type=identity_type,
        permissions=permissions,
        auth_source="clerk",
    )
