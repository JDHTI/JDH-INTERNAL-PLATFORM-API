"""Microsoft Graph adapter of the directory: users, managers and photos (``User.Read.All``)."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

from app.core.errors import DependencyUnavailableError
from app.infrastructure.graph_auth import GRAPH_URL, GraphAppClient
from app.modules.directory.domain.entities import GraphPhoto, GraphPhotoMeta, GraphUser

# ``birthday`` is left out on purpose: with application permissions it makes Graph fail
# the whole listing. The birth dates come from the intranet import (and later the DWH).
USER_FIELDS = (
    "id,displayName,mail,userPrincipalName,jobTitle,department,officeLocation,"
    "accountEnabled,employeeHireDate,createdDateTime,userType"
)
_PAGE_LIMIT = 100


def _segment(value: str) -> str:
    return quote(value, safe="")


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def graph_user(item: dict) -> GraphUser:
    """Build a user from one item of ``GET /users``.

    Args:
        item (dict): Graph JSON object.

    Returns:
        GraphUser: The user; the hire date falls back to the account creation date.
    """

    return GraphUser(
        object_id=item["id"],
        display_name=item.get("displayName"),
        mail=item.get("mail"),
        user_principal_name=item.get("userPrincipalName"),
        job_title=item.get("jobTitle"),
        department=item.get("department"),
        office_location=item.get("officeLocation"),
        account_enabled=item.get("accountEnabled") is not False,
        user_type=item.get("userType"),
        hire_date=_date(item.get("employeeHireDate")) or _date(item.get("createdDateTime")),
    )


class GraphDirectoryClient(GraphAppClient):
    """Read the tenant's users with client credentials."""

    credentials_label = "del directorio"

    async def list_users(self) -> list[GraphUser]:
        """Every user of the tenant, following the pages.

        Returns:
            list[GraphUser]: Members, guests, enabled and disabled accounts.
        """

        url = f"{GRAPH_URL}/users?$select={USER_FIELDS}&$top=999"
        users: list[GraphUser] = []
        for _ in range(_PAGE_LIMIT):
            response = await self._request("GET", url)
            assert response is not None
            body = response.json()
            users.extend(graph_user(item) for item in body.get("value", []) if item.get("id"))
            url = body.get("@odata.nextLink")
            if not url:
                return users
        raise DependencyUnavailableError("El directorio tiene demasiadas páginas de usuarios")

    async def get_manager(self, object_id: str) -> str | None:
        """Object id of the user's manager.

        Args:
            object_id (str): Entra object id.

        Returns:
            str | None: The manager's object id, or ``None`` when there is none.
        """

        response = await self._request(
            "GET",
            f"{GRAPH_URL}/users/{_segment(object_id)}/manager?$select=id,displayName",
            missing=(404,),
        )
        return None if response is None else response.json().get("id")

    async def get_photo_meta(self, object_id: str) -> GraphPhotoMeta | None:
        """What Graph says about the photo, without downloading it.

        Args:
            object_id (str): Entra object id.

        Returns:
            GraphPhotoMeta | None: Its version, or ``None`` when the user has no photo.
        """

        response = await self._request(
            "GET", f"{GRAPH_URL}/users/{_segment(object_id)}/photo", missing=(404,)
        )
        if response is None:
            return None
        return GraphPhotoMeta(etag=response.json().get("@odata.mediaEtag"))

    async def get_photo(self, object_id: str, size: int) -> GraphPhoto | None:
        """The photo at a size, falling back to the original when Graph lacks that size.

        Args:
            object_id (str): Entra object id.
            size (int): Side in pixels (96 or 432).

        Returns:
            GraphPhoto | None: The photo, or ``None`` when there is none.
        """

        user = f"{GRAPH_URL}/users/{_segment(object_id)}"
        response = await self._request(
            "GET", f"{user}/photos/{size}x{size}/$value", missing=(404,)
        ) or await self._request("GET", f"{user}/photo/$value", missing=(404,))
        if response is None:
            return None
        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
        return GraphPhoto(content=response.content, content_type=content_type)
