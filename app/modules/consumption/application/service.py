"""Use cases for read-only organizational data consumption."""

from __future__ import annotations

from app.modules.consumption.application.cursor import ClientCursorCodec
from app.modules.consumption.domain.entities import ClientPage
from app.modules.consumption.domain.ports import ClientDataSource


class ConsumptionService:
    """Coordinate cursor pagination without exposing the data provider."""

    def __init__(self, data_source: ClientDataSource, cursor_codec: ClientCursorCodec) -> None:
        """Create the service with a client data source and cursor codec.

        Args:
            data_source (ClientDataSource): Port used to read client rows.
            cursor_codec (ClientCursorCodec): Codec used to validate page cursors.
        """

        self.data_source = data_source
        self.cursor_codec = cursor_codec

    async def list_clients(self, limit: int, cursor: str | None = None) -> ClientPage:
        """Return one cursor-paginated page of clients.

        Args:
            limit (int): Maximum number of rows in the requested page.
            cursor (str | None): Cursor returned by the previous page.

        Returns:
            ClientPage: Client rows and the cursor for the next page.

        Raises:
            InvalidCursor: If ``cursor`` does not belong to the configured provider.
        """

        after_key = self.cursor_codec.decode(cursor) if cursor else None
        items, has_more = await self.data_source.fetch_clients(after_key, limit)
        next_cursor = self.cursor_codec.encode(items[-1].client_key) if has_more and items else None
        return ClientPage(items=tuple(items), next_cursor=next_cursor, has_more=has_more)
