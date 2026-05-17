import subprocess
import threading

import pytest
from auth_extractor import (
    NoValidLinksFound,
    _filename_from_url,
    _resolve_secrets,
    _wid,
    execute_with_retry,
)

# ---------------------------------------------------------------------------
# _wid
# ---------------------------------------------------------------------------


def test_wid_returns_empty_string_in_main_thread():
    result = _wid()
    assert result == ""


def test_wid_returns_worker_prefix_in_worker_thread():
    result_holder = {}

    def worker_func():
        result_holder["wid"] = _wid()

    thread = threading.Thread(target=worker_func, name="auth-worker-2")
    thread.start()
    thread.join()

    assert result_holder["wid"] == "[worker-2] "


def test_wid_returns_empty_string_in_non_worker_thread():
    result_holder = {}

    def other_func():
        result_holder["wid"] = _wid()

    thread = threading.Thread(target=other_func, name="some-other-thread")
    thread.start()
    thread.join()

    assert result_holder["wid"] == ""


# ---------------------------------------------------------------------------
# _filename_from_url
# ---------------------------------------------------------------------------


def test_filename_from_url_simple():
    assert _filename_from_url("https://example.com/doc.pdf") == "doc.pdf"


def test_filename_from_url_with_query_string():
    assert _filename_from_url("https://example.com/doc.pdf?token=abc123") == "doc.pdf"


def test_filename_from_url_with_path():
    assert (
        _filename_from_url("https://example.com/docs/guides/manual.pdf") == "manual.pdf"
    )


def test_filename_from_url_no_filename():
    assert _filename_from_url("https://example.com/") == "download.pdf"


def test_filename_from_url_no_filename_with_query():
    assert _filename_from_url("https://example.com/?token=abc") == "download.pdf"


def test_filename_from_url_trailing_slash():
    assert _filename_from_url("https://example.com/docs/") == "download.pdf"


# ---------------------------------------------------------------------------
# _resolve_secrets
# ---------------------------------------------------------------------------


def test_resolve_secrets_passthrough_plain_values():
    result = _resolve_secrets({"user": "alice", "pass": "secret123"})
    assert result == {"user": "alice", "pass": "secret123"}


def test_resolve_secrets_none_values_passthrough():
    result = _resolve_secrets({"optional_key": None})
    assert result == {"optional_key": None}


def test_resolve_secrets_empty_string_passthrough():
    result = _resolve_secrets({"key": ""})
    assert result == {"key": ""}


def test_resolve_secrets_mixed_plain_and_none():
    result = _resolve_secrets({"user": "alice", "token": None})
    assert result == {"user": "alice", "token": None}


def test_resolve_secrets_single_op_ref(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "SECRET_KEY=resolved_value\n"

    result = _resolve_secrets({"SECRET_KEY": "op://vault/item/field"})

    assert result == {"SECRET_KEY": "resolved_value"}
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args[0][0] == ["op", "inject"]
    assert "op://vault/item/field" in call_args[1]["input"]


def test_resolve_secrets_multiple_op_refs(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "USER=alice\nPASS=secret\n"

    result = _resolve_secrets(
        {"USER": "op://vault/user/field", "PASS": "op://vault/pass/field"}
    )

    assert result == {"USER": "alice", "PASS": "secret"}
    mock_run.assert_called_once()


def test_resolve_secrets_mixed_op_and_plain(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "SECRET=resolved\n"

    result = _resolve_secrets(
        {"SECRET": "op://vault/secret/field", "PLAIN": "plaintext"}
    )

    assert result == {"SECRET": "resolved", "PLAIN": "plaintext"}
    mock_run.assert_called_once()


def test_resolve_secrets_value_with_equals_sign(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "PASSWORD=pass=word123\n"

    result = _resolve_secrets({"PASSWORD": "op://vault/pass/field"})

    assert result == {"PASSWORD": "pass=word123"}


def test_resolve_secrets_op_cli_error_retries(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.side_effect = [
        subprocess.CompletedProcess([], 1, "", "auth failed"),
        subprocess.CompletedProcess([], 0, "KEY=value\n", ""),
    ]

    result = _resolve_secrets({"KEY": "op://vault/item/field"}, retries=2)

    assert result == {"KEY": "value"}
    assert mock_run.call_count == 2


def test_resolve_secrets_op_cli_fails_after_retries(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value = subprocess.CompletedProcess([], 1, "", "persistent error")

    with pytest.raises(subprocess.CalledProcessError):
        _resolve_secrets({"KEY": "op://vault/item/field"}, retries=2)

    assert mock_run.call_count == 2


def test_resolve_secrets_missing_key_in_output(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "OTHER_KEY=value\n"

    with pytest.raises(RuntimeError, match="template/output mismatch"):
        _resolve_secrets({"KEY": "op://vault/item/field"})


def test_resolve_secrets_ignores_empty_lines_in_output(mocker):
    mock_run = mocker.patch("auth_extractor.subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "\nKEY=value\n\n"

    result = _resolve_secrets({"KEY": "op://vault/item/field"})

    assert result == {"KEY": "value"}


# ---------------------------------------------------------------------------
# execute_with_retry
# ---------------------------------------------------------------------------


def test_execute_with_retry_succeeds_immediately():
    result = execute_with_retry(lambda: 42)
    assert result == 42


def test_execute_with_retry_retries_on_no_valid_links():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise NoValidLinksFound("no links")
        return "ok"

    assert execute_with_retry(flaky, retries=3) == "ok"
    assert calls["n"] == 3


def test_execute_with_retry_raises_after_max_retries():
    def always_fail():
        raise NoValidLinksFound("still nothing")

    with pytest.raises(NoValidLinksFound):
        execute_with_retry(always_fail, retries=3)


def test_execute_with_retry_raises_immediately_on_unknown_exception():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("unexpected")

    with pytest.raises(ValueError):
        execute_with_retry(boom, retries=3)

    assert calls["n"] == 1  # no retries on unknown exception


def test_execute_with_retry_calls_on_retry_hook():
    calls = {"func": 0, "hook": 0}

    def flaky():
        calls["func"] += 1
        if calls["func"] < 2:
            raise NoValidLinksFound("retry")
        return "done"

    def hook():
        calls["hook"] += 1

    result = execute_with_retry(flaky, retries=2, on_retry=hook)

    assert result == "done"
    assert calls["func"] == 2
    assert calls["hook"] == 1


def test_execute_with_retry_hook_not_called_on_success():
    calls = {"hook": 0}

    def hook():
        calls["hook"] += 1

    result = execute_with_retry(lambda: "ok", on_retry=hook)

    assert result == "ok"
    assert calls["hook"] == 0


def test_execute_with_retry_hook_not_called_after_max_retries():
    """Hook is only called between retries, not after the final attempt."""
    calls = {"hook": 0}

    def flaky():
        raise NoValidLinksFound("fail")

    def hook():
        calls["hook"] += 1

    with pytest.raises(NoValidLinksFound):
        execute_with_retry(flaky, retries=3, on_retry=hook)

    # Hook called after 1st and 2nd failures, but not after 3rd (the final one)
    assert calls["hook"] == 2


def test_execute_with_retry_hook_failure_logged_not_raised(mocker):
    """If the on_retry hook fails, it's logged but execution continues."""
    mocker.patch("auth_extractor.logger")
    calls = {"func": 0}

    def flaky():
        calls["func"] += 1
        if calls["func"] < 2:
            raise NoValidLinksFound("retry")
        return "done"

    def failing_hook():
        raise RuntimeError("hook broke")

    result = execute_with_retry(flaky, retries=2, on_retry=failing_hook)

    assert result == "done"  # Still succeeds despite hook failure
