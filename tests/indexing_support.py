"""Submit through the HTTP contract, run a queued delivery, then poll via the tenant API."""
from unittest.mock import AsyncMock, patch
from uuid import UUID

from app.providers.vectorstores.pgvector import PgVectorStore
from app.services.indexing_service import DocumentIndexFailed, IndexingService
from app.utils.markdown_splitter import MarkdownStructuredSplitter


async def upload_and_index(app, client, body, *, embedding=None, keyword=None, store=PgVectorStore, headers=None):
    with patch("app.services.index_dispatch.index_document_task.delay") as publish:
        response = await client.post("documents/upload", json=body, headers=headers)
    accepted = response.raise_for_status().json()["data"]
    assert accepted["status"] == 3 and accepted["chunk_count"] == 0
    publish.assert_called_once_with(accepted["document_id"])
    settings = app.state.settings
    async with app.state.session_factory() as session:
        service = IndexingService(
            MarkdownStructuredSplitter(settings.chunk_size, settings.chunk_overlap, settings.table_max_rows_per_chunk),
            embedding or app.state.embedding_provider, store(session), keyword or AsyncMock(),
        )
        try:
            await service.index_existing_document(session, UUID(accepted["document_id"]), final_attempt=True)
        except DocumentIndexFailed:
            pass
    return await client.get("documents/" + accepted["document_id"], headers=headers)
