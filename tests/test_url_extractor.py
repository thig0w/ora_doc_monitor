import requests
import url_extractor


def _make_mock_progressbar(mocker):
    mock_pb = mocker.patch("url_extractor.progressbar")
    mock_pb.track.side_effect = lambda iterable, **kwargs: iter(iterable)
    return mock_pb


# ---------------------------------------------------------------------------
# download_pdfs
# ---------------------------------------------------------------------------


def test_download_pdfs_failed_page_fetch_does_not_crash(tmp_path, mocker):
    """If the index page request fails, log the error and continue — no crash."""
    _make_mock_progressbar(mocker)
    mocker.patch("url_extractor.os.getcwd", return_value=str(tmp_path))
    mocker.patch(
        "url_extractor.requests.get",
        side_effect=requests.exceptions.ConnectionError("unreachable"),
    )

    sources = [{"desc": "test_docs", "doc_id": "https://example.com/books.html"}]
    url_extractor.download_pdfs(sources)  # must not raise


def test_download_pdfs_successful_download_writes_file(tmp_path, mocker):
    """A valid page with one PDF link results in the file being written."""
    _make_mock_progressbar(mocker)
    mocker.patch("url_extractor.os.getcwd", return_value=str(tmp_path))
    mocker.patch("url_extractor.sleep")  # skip wait between downloads

    html = '<a href="https://example.com/doc.pdf">Doc</a>'
    pdf_bytes = b"%PDF-fake"

    page_response = mocker.MagicMock()
    page_response.raise_for_status.return_value = None
    page_response.text = html

    pdf_response = mocker.MagicMock()
    pdf_response.raise_for_status.return_value = None
    pdf_response.content = pdf_bytes

    mocker.patch(
        "url_extractor.requests.get", side_effect=[page_response, pdf_response]
    )

    sources = [{"desc": "test_docs", "doc_id": "https://example.com/books.html"}]
    url_extractor.download_pdfs(sources)

    work_dir = tmp_path / "test_docs_work"
    assert (work_dir / "doc.pdf").read_bytes() == pdf_bytes


def test_download_pdfs_retries_failed_pdf(tmp_path, mocker):
    """A PDF download failure re-queues the link; on the second attempt it succeeds."""
    _make_mock_progressbar(mocker)
    mocker.patch("url_extractor.os.getcwd", return_value=str(tmp_path))
    mocker.patch("url_extractor.sleep")

    html = '<a href="https://example.com/guide.pdf">Guide</a>'
    pdf_bytes = b"%PDF-real"

    page_response = mocker.MagicMock()
    page_response.raise_for_status.return_value = None
    page_response.text = html

    fail_response = mocker.MagicMock()
    fail_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500")

    ok_response = mocker.MagicMock()
    ok_response.raise_for_status.return_value = None
    ok_response.content = pdf_bytes

    # First call: page fetch succeeds. Second: PDF fails. Third: PDF succeeds.
    mocker.patch(
        "url_extractor.requests.get",
        side_effect=[page_response, fail_response, ok_response],
    )

    sources = [{"desc": "test_docs", "doc_id": "https://example.com/books.html"}]
    url_extractor.download_pdfs(sources)

    work_dir = tmp_path / "test_docs_work"
    assert (work_dir / "guide.pdf").read_bytes() == pdf_bytes
