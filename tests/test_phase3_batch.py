from unittest.mock import MagicMock

from extraction.batch_processor import BatchProcessor
from parsing.parser_router import ParserRouter

from tests.llm_fakes import FakeLLMRouter

class TestBatchProcessor:
    """Test batch processing of papers."""

    def test_batch_processor_initialization(self):
        """Test batch processor can be initialized."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()

        processor = BatchProcessor(mock_llm, parser_router)

        assert processor.llm_router == mock_llm
        assert processor.parser_router == parser_router

    def test_batch_processor_get_job_status(self):
        """Test retrieving batch job status."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()

        processor = BatchProcessor(mock_llm, parser_router)

        # Process with empty list should return status
        status = processor.process_papers([], {}, job_id="test_job")

        assert status.job_id == "test_job"
        assert status.papers_total == 0
        assert status.status == "completed"

        # Should be retrievable
        retrieved = processor.get_job_status("test_job")
        assert retrieved is not None
        assert retrieved.job_id == "test_job"

    def test_batch_processor_persists_extraction_results(self, tmp_path):
        """Test batch processor can persist successful extraction results."""
        from storage.metadata_db import MetadataDB

        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        parser_router.parse = MagicMock(
            return_value=MagicMock(text="Paper text about neural networks")
        )
        db = MetadataDB(str(tmp_path / "metadata.duckdb"))
        processor = BatchProcessor(mock_llm, parser_router, metadata_db=db)

        status = processor.process_papers(
            ["paper_001"],
            {"paper_001": str(tmp_path / "paper.pdf")},
            job_id="persist_job",
        )

        assert status.status == "completed"
        assert status.papers_processed == 1
        assert len(db.get_paper_extractions("paper_001")) == 1
        job = db.get_batch_job("persist_job")
        assert job is not None
        assert job["status"] == "completed"
        items = db.get_batch_job_items("persist_job")
        assert items[0]["status"] == "completed"
        assert len(db.list_entity_embeddings()) == 1
        db.close()

    def test_batch_processor_resumes_completed_items_from_storage(self, tmp_path):
        """Test completed items are skipped when a persistent job is resumed."""
        from storage.metadata_db import MetadataDB

        db_path = tmp_path / "metadata.duckdb"
        db = MetadataDB(str(db_path))
        db.upsert_batch_job("resume_job", "processing", papers_total=1, papers_processed=1)
        db.upsert_batch_job_item("resume_job", "paper_001", str(tmp_path / "paper.pdf"), "completed")
        db.close()

        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        parser_router.parse = MagicMock(side_effect=AssertionError("should not reparse"))
        processor = BatchProcessor(
            mock_llm,
            parser_router,
            metadata_db_factory=lambda: MetadataDB(str(db_path)),
        )

        status = processor.process_papers(
            ["paper_001"],
            {"paper_001": str(tmp_path / "paper.pdf")},
            job_id="resume_job",
        )

        assert status.status == "completed"
        assert status.papers_processed == 1
        assert parser_router.parse.call_count == 0

    def test_batch_processor_marks_completed_with_errors(self, tmp_path):
        """Test partial failures are visible in durable aggregate status."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        processor = BatchProcessor(mock_llm, parser_router)

        status = processor.process_papers(
            ["paper_001"],
            {},
            job_id="missing_pdf_job",
        )

        assert status.status == "completed_with_errors"
        assert status.papers_failed == 1

    def test_batch_processor_retries_failed_parse(self, tmp_path):
        """Test batch processor retries transient parser failures."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        parser_router.parse = MagicMock(
            side_effect=[
                RuntimeError("temporary parse failure"),
                MagicMock(text="Paper text about neural networks"),
            ]
        )
        processor = BatchProcessor(mock_llm, parser_router, max_retries=1)

        status = processor.process_papers(
            ["paper_001"],
            {"paper_001": str(tmp_path / "paper.pdf")},
            job_id="retry_job",
        )

        assert status.papers_processed == 1
        assert status.papers_failed == 0
        assert parser_router.parse.call_count == 2


