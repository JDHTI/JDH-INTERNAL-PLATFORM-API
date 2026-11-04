"""The Graph adapter of the directory, against a scripted Graph."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.core.errors import DependencyUnavailableError
from app.modules.directory.infrastructure.graph import USER_FIELDS, GraphDirectoryClient

GRAPH = "https://graph.microsoft.com/v1.0"


class Graph:
    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok{self.tokens}", "expires_in": 3600}
            )
        self.requests.append(request)
        return self.handler(request)


def client(handler) -> tuple[GraphDirectoryClient, Graph]:
    graph = Graph(handler)
    http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
    return GraphDirectoryClient("tenant", "client", "secret", http=http), graph


@pytest.mark.asyncio
async def test_users_are_listed_across_pages() -> None:
    def handler(request):
        if "skiptoken" in str(request.url):
            return httpx.Response(200, json={"value": [{"id": "b", "accountEnabled": False}]})
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "a",
                        "displayName": " Ana  María ",
                        "mail": "Ana@JDH.com.co",
                        "userType": "Member",
                        "employeeHireDate": "2024-03-01T00:00:00Z",
                        "createdDateTime": "2020-01-01T10:00:00Z",
                    }
                ],
                "@odata.nextLink": f"{GRAPH}/users?$skiptoken=x",
            },
        )

    api, graph = client(handler)
    users = await api.list_users()

    assert [user.object_id for user in users] == ["a", "b"]
    assert users[0].email == "ana@jdh.com.co" and users[0].hire_date == date(2024, 3, 1)
    assert users[0].name == "Ana María"
    assert users[1].account_enabled is False and users[1].name == "b"
    first = graph.requests[0].url
    assert first.params["$select"] == USER_FIELDS and first.params["$top"] == "999"
    assert "birthday" not in USER_FIELDS
    assert graph.tokens == 1


@pytest.mark.asyncio
async def test_the_hire_date_falls_back_to_the_account_creation() -> None:
    api, _ = client(
        lambda r: httpx.Response(
            200, json={"value": [{"id": "a", "createdDateTime": "2021-06-15T08:00:00Z"}]}
        )
    )

    assert (await api.list_users())[0].hire_date == date(2021, 6, 15)


@pytest.mark.asyncio
async def test_missing_manager_and_photo_are_none() -> None:
    api, _ = client(lambda r: httpx.Response(404, json={"error": {"code": "ResourceNotFound"}}))

    assert await api.get_manager("a") is None
    assert await api.get_photo_meta("a") is None
    assert await api.get_photo("a", 96) is None


@pytest.mark.asyncio
async def test_manager_and_photo_are_read() -> None:
    def handler(request):
        path = request.url.path
        if path.endswith("/manager"):
            return httpx.Response(200, json={"id": "boss", "displayName": "Jefe"})
        if path.endswith("/photo"):
            return httpx.Response(200, json={"@odata.mediaEtag": 'W/"abc"'})
        if "/photos/96x96/" in path:
            return httpx.Response(404, json={})
        return httpx.Response(200, content=b"jpeg", headers={"content-type": "image/jpeg"})

    api, graph = client(handler)

    assert await api.get_manager("a") == "boss"
    assert (await api.get_photo_meta("a")).etag == 'W/"abc"'
    photo = await api.get_photo("a", 96)
    assert photo.content == b"jpeg" and photo.content_type == "image/jpeg"
    # 96x96 missing: it fell back to the original photo.
    assert graph.requests[-1].url.path.endswith("/photo/$value")


@pytest.mark.asyncio
async def test_a_rejected_token_is_renewed_and_server_errors_surface() -> None:
    def handler(request):
        if request.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={})
        return httpx.Response(503, text="busy")

    api, graph = client(handler)

    with pytest.raises(DependencyUnavailableError, match="503"):
        await api.list_users()
    assert graph.tokens == 2


@pytest.mark.asyncio
async def test_bad_credentials_name_the_directory() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    api = GraphDirectoryClient("tenant", "client", "wrong", http=http)

    with pytest.raises(DependencyUnavailableError, match="del directorio"):
        await api.list_users()
