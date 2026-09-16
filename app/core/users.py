"""
Phase 6 — user store.

In-memory only, by design, for this phase — same pattern as
approval_store.py's proposals. A restart resets users back to the
seed list below. This is fine for local development and demoing the
RBAC logic; a real deployment needs this backed by a database.

Roles match the original spec's table:
  Viewer:    read only
  Developer: read, investigate, propose fixes, approve/execute
  Reviewer:  read, can reject/approve proposals, cannot write code
  Admin:     everything
"""

from app.core.security import hash_password, verify_password

ROLES = {"viewer", "developer", "reviewer", "admin"}


class User:
    def __init__(self, username: str, hashed_password: str, role: str):
        self.username = username
        self.hashed_password = hashed_password
        self.role = role


# Seed users for local development. CHANGE THESE before deploying
# anywhere beyond your own machine — these are intentionally simple
# placeholder credentials, not meant to survive past local testing.
_USERS: dict[str, User] = {
    "admin": User("admin", hash_password("admin123"), "admin"),
    "developer": User("developer", hash_password("dev123"), "developer"),
    "reviewer": User("reviewer", hash_password("review123"), "reviewer"),
    "viewer": User("viewer", hash_password("view123"), "viewer"),
}


def get_user(username: str) -> User | None:
    return _USERS.get(username)


def authenticate(username: str, password: str) -> User | None:
    user = get_user(username)
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_user(username: str, password: str, role: str) -> User:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}. Must be one of {ROLES}")
    if username in _USERS:
        raise ValueError(f"User {username} already exists")
    user = User(username, hash_password(password), role)
    _USERS[username] = user
    return user