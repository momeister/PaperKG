from storage.file_manager import FileManager


def test_file_manager_save_load_and_exists(tmp_path) -> None:
    manager = FileManager(tmp_path)
    paper_id = "arxiv:1234.5678"
    content = b"%PDF-1.4 fake"

    path = manager.save_pdf(paper_id, content, version=2)

    assert path.exists()
    assert manager.exists(paper_id, version=2)
    assert manager.load_pdf(paper_id, version=2) == content
    assert manager.get_size_bytes(paper_id, version=2) == len(content)


def test_file_manager_list_and_delete(tmp_path) -> None:
    manager = FileManager(tmp_path)
    manager.save_pdf("arxiv:1", b"x", version=1)

    listed = manager.list_papers()
    assert ("arxiv_1", 1) in listed

    assert manager.delete("arxiv:1", version=1) is True
    assert manager.exists("arxiv:1", version=1) is False
