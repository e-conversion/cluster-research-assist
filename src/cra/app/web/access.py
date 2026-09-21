"""Deny by default: only the landing page, static files, health, config and
the login routes answer without an active user."""

PUBLIC_PATHS = frozenset({"", "/", "/api/health", "/api/config"})
PUBLIC_PREFIXES = ("/static/", "/auth/")


def relative_path(path: str, base_path: str) -> str:
    if base_path and path.startswith(base_path):
        return path[len(base_path) :]
    return path


def is_public(path: str, base_path: str) -> bool:
    rel = relative_path(path, base_path)
    return rel in PUBLIC_PATHS or rel.startswith(PUBLIC_PREFIXES)
