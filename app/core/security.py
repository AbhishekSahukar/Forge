"""
Phase 6 — password hashing and JWT helpers.

Uses `bcrypt` directly rather than passlib — passlib is unmaintained
and its bcrypt backend breaks against current bcrypt releases (hit
this directly while building this file: passlib 1.7.4 crashes against
bcrypt 5.x with an unrelated-looking "password cannot be longer than
72 bytes" error). One less unmaintained dependency, same result.
"""

import datetime

import bcrypt
import jwt

from app.core.config import settings


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def create_access_token(subject: str, role: str) -> str:
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=settings.jwt_expire_minutes
    )
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError (or a subclass) on any invalid/expired token —
    callers are expected to catch and turn that into a 401."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])