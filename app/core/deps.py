"""
Phase 6 — FastAPI dependencies for authentication and RBAC.

get_current_user: extracts and validates the JWT from the Authorization
header (Swagger UI's "Authorize" button uses this automatically once
/auth/login is wired to the OAuth2PasswordBearer scheme below).

require_role(*roles): a dependency factory — use it per-endpoint to
say exactly which roles may call it, e.g.
    @router.post(..., dependencies=[Depends(require_role("developer", "admin"))])
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_access_token
from app.core.users import User, get_user

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = get_user(username)
    if user is None:
        raise credentials_exception
    return user


def require_role(*allowed_roles: str):
    """
    Usage: dependencies=[Depends(require_role("developer", "admin"))]
    Raises 403 if the authenticated user's role isn't in allowed_roles.
    Authorization happens here, at the API boundary — not trusted to
    the agents themselves, per the original spec's requirement.
    """

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not permitted to perform this action "
                f"(requires one of: {', '.join(allowed_roles)})",
            )
        return user

    return _check