"""Local development login: everyone is the configured user, or whoever a
trusted proxy header names. No allow-list; the user is created on first use."""

from cra.app.auth.principal import LoginDenied, LoginOutcome, SessionState
from cra.app.history.repository import Repository
from cra.config.settings import Settings

ISSUER = "dev"


class DevProvider:
    name = "dev"

    def __init__(self, settings: Settings, repo: Repository) -> None:
        self._header = settings.auth_user_header
        self._user = settings.auth_dev_user
        self._admins = {a.strip().lower() for a in settings.auth_admins}
        self._repo = repo

    async def start(self) -> None:
        return None

    async def login(
        self, session: SessionState, headers: dict[str, str]
    ) -> LoginOutcome:
        name = headers.get(self._header.lower(), "") if self._header else self._user
        name = name.strip()
        if not name:
            return LoginOutcome(denied=LoginDenied.FAILED)
        identity = await self._repo.get_identity(ISSUER, name)
        if identity is None:
            user = await self._repo.create_user(name)
            await self._repo.add_identity(ISSUER, name, user.id)
            return LoginOutcome(
                user_id=user.id,
                bound=True,
                grants_admin=name.lower() in self._admins,
            )
        existing = await self._repo.get_user(identity.user_id)
        if existing is None or not existing.is_active:
            return LoginOutcome(denied=LoginDenied.INACTIVE)
        return LoginOutcome(user_id=existing.id)

    async def callback(
        self, session: SessionState, args: dict[str, str]
    ) -> LoginOutcome:
        return LoginOutcome(denied=LoginDenied.FAILED)

    def logout_url(self, post_logout_uri: str) -> str | None:
        return None
