import subprocess

import pytest
from doc_extractor import NoValidLinksFound, _resolve_secret, execute_with_retry
from selenium.common.exceptions import StaleElementReferenceException

# ---------------------------------------------------------------------------
# _resolve_secret
# ---------------------------------------------------------------------------


def test_resolve_secret_passthrough():
    assert _resolve_secret("plain_value") == "plain_value"


def test_resolve_secret_none():
    assert _resolve_secret(None) is None


def test_resolve_secret_empty_string():
    assert _resolve_secret("") == ""


def test_resolve_secret_calls_op_cli(mocker):
    mock_run = mocker.patch("doc_extractor.subprocess.run")
    mock_run.return_value.stdout = "resolved_secret\n"

    result = _resolve_secret("op://vault/item/field")

    mock_run.assert_called_once_with(
        ["op", "read", "op://vault/item/field"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == "resolved_secret"


def test_resolve_secret_op_cli_error_propagates(mocker):
    mocker.patch(
        "doc_extractor.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "op"),
    )
    with pytest.raises(subprocess.CalledProcessError):
        _resolve_secret("op://vault/item/field")


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


def test_execute_with_retry_retries_on_stale_element():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise StaleElementReferenceException()
        return "done"

    assert execute_with_retry(flaky, retries=3) == "done"
    assert calls["n"] == 2


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
