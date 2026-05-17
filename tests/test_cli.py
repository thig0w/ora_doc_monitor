import threading

import cli


def test_read_json_returns_expected_keys():
    data = cli.read_json()
    assert "auth_req" in data
    assert "noauth_req" in data


def test_read_json_auth_req_structure():
    data = cli.read_json()
    for entry in data["auth_req"]:
        assert "desc" in entry
        assert "doc_id" in entry


def test_read_json_noauth_req_structure():
    data = cli.read_json()
    for entry in data["noauth_req"]:
        assert "desc" in entry
        assert "doc_id" in entry
        assert entry["doc_id"].startswith("http")


# ---------------------------------------------------------------------------
# _auth_download_and_diff
# ---------------------------------------------------------------------------


def test_auth_download_and_diff_calls_download_and_diff(mocker):
    """Test that _auth_download_and_diff calls download_docs and diff when enabled."""
    mock_download = mocker.patch("cli.download_auth_docs")
    mock_diff = mocker.patch("cli.diff_auth_folders")
    login_done = threading.Event()

    sources = [{"desc": "test", "doc_id": "12345"}]
    auth_result = [True]

    cli._auth_download_and_diff(
        sources,
        headed=False,
        auth_result=auth_result,
        run_diff=True,
        login_done=login_done,
        workers=2,
    )

    mock_download.assert_called_once_with(
        sources, headed=False, result=auth_result, login_done=login_done, workers=2
    )
    mock_diff.assert_called_once()


def test_auth_download_and_diff_skips_diff_when_disabled(mocker):
    """Test that diff is skipped when run_diff=False."""
    mock_download = mocker.patch("cli.download_auth_docs")
    mock_diff = mocker.patch("cli.diff_auth_folders")
    login_done = threading.Event()

    sources = [{"desc": "test", "doc_id": "12345"}]
    auth_result = [True]

    cli._auth_download_and_diff(
        sources,
        headed=False,
        auth_result=auth_result,
        run_diff=False,
        login_done=login_done,
        workers=2,
    )

    mock_download.assert_called_once()
    mock_diff.assert_not_called()


def test_auth_download_and_diff_skips_diff_when_download_fails(mocker):
    """Test that diff is skipped if download failed (auth_result[0] is False)."""
    mock_download = mocker.patch("cli.download_auth_docs")
    mock_diff = mocker.patch("cli.diff_auth_folders")
    login_done = threading.Event()

    sources = [{"desc": "test", "doc_id": "12345"}]
    auth_result = [False]  # Download failed

    cli._auth_download_and_diff(
        sources,
        headed=False,
        auth_result=auth_result,
        run_diff=True,
        login_done=login_done,
        workers=2,
    )

    mock_download.assert_called_once()
    mock_diff.assert_not_called()


# ---------------------------------------------------------------------------
# _noauth_download_and_diff
# ---------------------------------------------------------------------------


def test_noauth_download_and_diff_waits_for_login_event(mocker):
    """Test that noauth download waits for login_done event."""
    mock_download = mocker.patch("cli.download_pdfs")
    mocker.patch("cli.diff_noauth_folders")

    sources = [{"desc": "test", "doc_id": "https://example.com"}]
    login_done = threading.Event()

    # Record when download was called
    download_time_holder = {}

    def record_download(*args, **kwargs):
        download_time_holder["called"] = True

    mock_download.side_effect = record_download

    # Spawn the function in a thread so we can set the event
    def run_noauth():
        cli._noauth_download_and_diff(sources, run_diff=True, login_done=login_done)

    thread = threading.Thread(target=run_noauth)
    thread.start()

    # Give the thread a moment to block on the event
    import time

    time.sleep(0.1)

    # Download should not have been called yet
    assert "called" not in download_time_holder

    # Now trigger the event
    login_done.set()
    thread.join(timeout=1)

    # Download should have been called
    mock_download.assert_called_once_with(sources)


def test_noauth_download_and_diff_no_wait_when_login_done_is_none(mocker):
    """Test that noauth download proceeds immediately when login_done is None."""
    mock_download = mocker.patch("cli.download_pdfs")
    mock_diff = mocker.patch("cli.diff_noauth_folders")

    sources = [{"desc": "test", "doc_id": "https://example.com"}]

    cli._noauth_download_and_diff(sources, run_diff=True, login_done=None)

    mock_download.assert_called_once_with(sources)
    mock_diff.assert_called_once_with(sources)


def test_noauth_download_and_diff_skips_diff_when_disabled(mocker):
    """Test that diff is skipped when run_diff=False."""
    mock_download = mocker.patch("cli.download_pdfs")
    mock_diff = mocker.patch("cli.diff_noauth_folders")
    login_done = threading.Event()
    login_done.set()

    sources = [{"desc": "test", "doc_id": "https://example.com"}]

    cli._noauth_download_and_diff(sources, run_diff=False, login_done=login_done)

    mock_download.assert_called_once()
    mock_diff.assert_not_called()


# ---------------------------------------------------------------------------
# get_docs (CLI command)
# ---------------------------------------------------------------------------


def test_get_docs_both_auth_and_noauth_default(mocker):
    """Test that both downloads run when no flags are specified."""
    mock_auth = mocker.patch("cli._auth_download_and_diff")
    mock_noauth = mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, [])

    mock_auth.assert_called_once()
    mock_noauth.assert_called_once()


def test_get_docs_auth_only(mocker):
    """Test that only auth download runs when --auth_docs flag is set."""
    mock_auth = mocker.patch("cli._auth_download_and_diff")
    mock_noauth = mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, ["--auth_docs"])

    mock_auth.assert_called_once()
    mock_noauth.assert_not_called()


def test_get_docs_noauth_only(mocker):
    """Test that only noauth download runs when --no_auth_docs flag is set."""
    mock_auth = mocker.patch("cli._auth_download_and_diff")
    mock_noauth = mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, ["--no_auth_docs"])

    mock_auth.assert_not_called()
    mock_noauth.assert_called_once()


def test_get_docs_download_only_skips_diff(mocker):
    """Test that diff is skipped when --download flag is set."""
    mock_auth = mocker.patch("cli._auth_download_and_diff")
    mock_noauth = mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, ["--download"])

    # Both should be called with run_diff=False
    auth_call = mock_auth.call_args
    assert auth_call[0][3] is False  # run_diff is 4th arg

    noauth_call = mock_noauth.call_args
    assert noauth_call[0][1] is False  # run_diff is 2nd arg


def test_get_docs_custom_worker_count(mocker):
    """Test that custom worker count is passed to auth download."""
    mock_auth = mocker.patch("cli._auth_download_and_diff")
    mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, ["--workers", "4"])

    auth_call = mock_auth.call_args
    assert auth_call[0][5] == 4  # workers is 6th positional arg


def test_get_docs_login_event_gates_noauth(mocker):
    """Test that noauth download is gated by login_done event when both are enabled."""
    mocker.patch("cli._auth_download_and_diff")
    mock_noauth = mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, [])

    # login_done should be passed to noauth (not set before invocation)
    noauth_call = mock_noauth.call_args
    login_event = noauth_call[0][2]  # login_done is 3rd positional arg
    assert isinstance(login_event, threading.Event)
    assert not login_event.is_set()


def test_get_docs_login_event_preset_for_noauth_only(mocker):
    """Test that login_done is preset when noauth-only is specified."""
    mocker.patch("cli._auth_download_and_diff")
    mock_noauth = mocker.patch("cli._noauth_download_and_diff")
    mocker.patch("cli.read_json", return_value={"auth_req": [], "noauth_req": []})
    mocker.patch("cli.progressbar")

    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(cli.get_docs, ["--no_auth_docs"])

    # login_done should be pre-set (already triggered)
    noauth_call = mock_noauth.call_args
    login_event = noauth_call[0][2]  # login_done is 3rd positional arg
    assert login_event.is_set()
