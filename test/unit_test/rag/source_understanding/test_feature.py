from __future__ import annotations

import pytest

from rag.source_understanding.feature import (
    SourceUnderstandingFeatureConfigurationError,
    book_pdf_source_understanding_enabled,
    book_source_understanding_enabled,
    source_understanding_enabled,
)


def test_feature_flag_is_explicit_and_disabled_by_default():
    assert source_understanding_enabled(None) is False
    assert source_understanding_enabled({}) is False
    assert source_understanding_enabled({"source_understanding": {}}) is False
    assert source_understanding_enabled({"source_understanding": {"enabled": True}}) is True


def test_feature_flag_rejects_ambiguous_truthy_values():
    with pytest.raises(SourceUnderstandingFeatureConfigurationError):
        source_understanding_enabled({"source_understanding": {"enabled": 1}})


def test_pr5_activation_is_scoped_to_book_pdfs():
    config = {"source_understanding": {"enabled": True}}

    assert book_source_understanding_enabled("book", config) is True
    assert book_source_understanding_enabled("paper", config) is False
    assert book_pdf_source_understanding_enabled("BOOK", config, filename="Novel.PDF") is True
    assert book_pdf_source_understanding_enabled("book", config, filename="Novel.docx") is False
