from __future__ import annotations

from api.db.services.task_service import _pdf_task_page_ranges
from common.constants import MAXIMUM_TASK_PAGE_NUMBER


def _book_doc(parser_config: dict) -> dict:
    return {
        "parser_id": "book",
        "parser_config": parser_config,
    }


def test_legacy_book_pdf_keeps_page_splitting():
    assert _pdf_task_page_ranges(_book_doc({"task_page_size": 12}), 25) == (
        (0, 12),
        (12, 24),
        (24, 25),
    )


def test_enabled_book_pdf_uses_one_whole_document_task():
    config = {
        "task_page_size": 3,
        "pages": [(5, 10)],
        "source_understanding": {"enabled": True},
    }

    assert _pdf_task_page_ranges(_book_doc(config), 125) == ((0, 125),)


def test_enabled_book_pdf_still_queues_when_page_count_cannot_be_read():
    config = {"source_understanding": {"enabled": True}}

    assert _pdf_task_page_ranges(_book_doc(config), 0) == (
        (0, MAXIMUM_TASK_PAGE_NUMBER),
    )
