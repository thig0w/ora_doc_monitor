import os
import queue
import re
import shutil
import subprocess
import threading
from time import sleep

from dotenv import load_dotenv
from interface import logger, progressbar
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pyotp import TOTP


class NoValidLinksFound(Exception):
    """Raised when a doc page renders without any downloadable link anchors.

    Signals that the collector ran to completion but found zero usable
    ``href`` values — usually a timing issue with the Oracle UI. Callers are
    expected to catch this and retry.
    """


load_dotenv()


# Identity domain OCID for the sso-domain dropdown option on MOS login.
SSO_DOMAIN_OCID = (
    "ocid1.domain.oc1..aaaaaaaaudqbjcd7jq2cnoexev47l7bforpvrjywhrglclqoqw32yldcry5a"
)

# URL that renders a MOS document page once authenticated.
DOC_DISPLAY_URL = "https://support.oracle.com/epmos/faces/DocumentDisplay?id="


def _resolve_secret(
    value: str | None, retries: int = 3, backoff: float = 1.5
) -> str | None:
    """Resolve a credential value, transparently dereferencing 1Password refs.

    If ``value`` is an ``op://vault/item/field`` reference, shells out to the
    ``op`` CLI to read the secret. Plain strings (and ``None``) are returned
    as-is so the caller can mix stored secrets and inline values freely.

    The ``op`` CLI fails intermittently (session re-auth, biometric timeout,
    throttling when several reads happen in a row), so non-zero exits are
    retried with linear backoff. The CLI's stderr is logged on every failure
    so a persistent error is visible before the final raise.
    """
    if not (value and value.startswith("op://")):
        return value

    last_err: subprocess.CalledProcessError | None = None
    for attempt in range(1, retries + 1):
        result = subprocess.run(
            ["op", "read", value], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return result.stdout.strip()

        stderr = (result.stderr or "").strip()
        logger.warning(
            f"op read failed for {value!r} (attempt {attempt}/{retries}, "
            f"exit {result.returncode}): {stderr or '<no stderr>'}"
        )
        last_err = subprocess.CalledProcessError(
            result.returncode,
            ["op", "read", value],
            output=result.stdout,
            stderr=result.stderr,
        )
        if attempt < retries:
            sleep(backoff * attempt)

    raise last_err


# Persistent base folder — always exists, never deleted
_base_path = os.path.join(os.getcwd(), "func_docs")
os.makedirs(_base_path, exist_ok=True)

# Working download folder — cleared at the start of download_docs
file_path = os.path.join(os.getcwd(), "func_docs_work")


def _login(page: Page) -> bool:
    """Log into MOS through the commercial cloud SSO flow.

    Reads MOSUSER/MOSPASS/MOSMFAKEY from the environment (optionally
    dereferencing 1Password refs), drives the Oracle sign-in screens
    (tenancy, identity domain, username, password, TOTP 2FA) and waits
    for the post-login redirect back to support.oracle.com to settle.

    Returns ``True`` on success and ``False`` if the required credentials
    are missing. All other failure modes raise the underlying Playwright
    exception to the caller.
    """
    mos_user = _resolve_secret(os.getenv("MOSUSER"))
    mos_pass = _resolve_secret(os.getenv("MOSPASS"))
    mos_mfa_raw = _resolve_secret(os.getenv("MOSMFAKEY"))

    if mos_user is None or mos_pass is None or mos_mfa_raw is None:
        logger.critical(
            "Please set MOSUSER, MOSPASS and MOSMFAKEY environment variables!"
        )
        return False

    mos_mfa_key = mos_mfa_raw.replace(" ", "")

    step_timeout = 45000

    logger.debug("Opening MOS sign-in page")
    page.goto("https://support.oracle.com/signin/", wait_until="domcontentloaded")

    # Each screen transition is guarded by a wait_for on the next screen's
    # distinctive element. JET does SPA-style view switches without URL
    # changes, so expect_navigation is not reliable — explicit element
    # waits are the barrier that actually matters here.
    sign_in_btn = page.get_by_role("button", name="Sign in with your commercial")
    sign_in_btn.wait_for(state="visible", timeout=step_timeout)
    logger.debug("Clicking 'Sign in with your commercial'")
    sign_in_btn.click()

    tenancy_field = page.get_by_role("textbox", name="Tenancy")
    tenancy_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug("Submitting tenancy")
    tenancy_field.fill("myoraclesupport")
    page.get_by_role("button", name="Continue").click()

    domain_dropdown = page.locator('[data-test-id="identity-domain-dropdown"]')
    domain_dropdown.wait_for(state="visible", timeout=step_timeout)
    logger.debug("Selecting identity domain")
    domain_dropdown.select_option(SSO_DOMAIN_OCID)
    page.get_by_role("button", name="Next").click()

    username_field = page.get_by_role("textbox", name="Username or email")
    username_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug("Submitting username")
    username_field.fill(mos_user)
    page.get_by_role("button", name="Next").click()

    pwd_field = page.locator('[id="idcs-auth-pwd-input|input"]')
    pwd_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug("Submitting password")
    pwd_field.fill(mos_pass)
    page.get_by_role("button", name="Sign In").click()

    passcode_field = page.get_by_role("textbox", name="Passcode")
    passcode_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug("Submitting 2fa")
    passcode_field.fill(TOTP(mos_mfa_key).now())
    page.get_by_role("button", name="Verify").click()

    logger.debug("Waiting for post-login redirect")
    page.wait_for_url("**/support.oracle.com/**", timeout=60000)
    # Oracle fires a few more redirects right after the SSO handshake lands —
    # networkidle waits deterministically for that cascade to settle.
    try:
        page.wait_for_load_state("networkidle", timeout=45000)
    except PlaywrightTimeoutError as e:
        logger.warning(f"Post-login networkidle timed out: {e} — continuing")
    return True


def _goto_doc(page: Page, doc_id: str) -> None:
    """Navigate to the ``DocumentDisplay`` page for the given MOS doc id.

    Uses the already-authenticated session on ``page``. Oracle routinely
    interrupts the initial request with an internal redirect, which makes
    Playwright raise ``NS_BINDING_ABORTED`` even though the target page ends
    up loading; that specific error is caught and the follow-up waits
    (``networkidle`` and the link-presence check in ``_collect_links``) are
    left to confirm whether the page actually rendered.
    """
    target = DOC_DISPLAY_URL + doc_id
    logger.debug(f"Navigating to DocumentDisplay?id={doc_id}")
    try:
        page.goto(target, wait_until="commit", timeout=60000)
    except PlaywrightError as e:
        if "NS_BINDING_ABORTED" in str(e):
            logger.debug(f"Goto aborted by Oracle redirect — continuing: {e}")
        else:
            raise
    try:
        page.wait_for_load_state("networkidle", timeout=45000)
    except PlaywrightTimeoutError as e:
        logger.warning(f"networkidle timed out for {doc_id}: {e} — continuing")


def _collect_links(page: Page, source_id: str) -> list[dict]:
    """Collect every downloadable link on the current MOS doc page.

    Waits until the ``a[data-oce-meta-data], a[data-ucm-meta-data]`` anchors
    have non-empty ``href`` values, then extracts ``href``, ``data_href``
    and text for each one in a single ``page.evaluate`` call so the DOM
    cannot re-render between individual reads.

    Raises :class:`NoValidLinksFound` if, after the wait, no anchor has a
    usable ``href`` — callers retry this case via :func:`execute_with_retry`.
    Returns the raw list of dicts (one per anchor).
    """
    logger.debug("Waiting for document links to populate...")
    page.wait_for_function(
        """() => {
            const els = document.querySelectorAll(
                'a[data-oce-meta-data], a[data-ucm-meta-data]'
            );
            if (!els.length) return false;
            for (const el of els) {
                const h = el.getAttribute('href');
                if (!h || !h.trim()) return false;
            }
            return true;
        }""",
        timeout=60000,
    )

    link_data = page.evaluate(
        """() => {
            const els = document.querySelectorAll(
                'a[data-oce-meta-data], a[data-ucm-meta-data]'
            );
            return Array.from(els).map((el) => ({
                href: el.getAttribute('href'),
                data_href: el.getAttribute('data-href'),
                text: (el.textContent || '').trim(),
            }));
        }"""
    )

    valid_links = [d for d in link_data if d.get("href")]
    if not valid_links:
        raise NoValidLinksFound(f"No links found for {source_id}")
    return link_data


def execute_with_retry(func, retries: int = 3, on_retry=None):
    """Call ``func()`` up to ``retries`` times, retrying only on transient errors.

    Retries when :class:`NoValidLinksFound` is raised (Oracle's async rendering
    sometimes leaves the link list momentarily empty) and re-raises on the
    last attempt. ``on_retry`` runs between attempts so the caller can recover
    page state (e.g. re-navigate) before the next call. Any other exception is
    propagated immediately — callers should not rely on this helper to swallow
    generic failures.
    """
    for retry in range(1, retries + 1):
        try:
            return func()
        except NoValidLinksFound as e:
            logger.warning(f"{e} — retrying ({retry}/{retries})")
            if retry == retries:
                raise
            if on_retry is not None:
                try:
                    on_retry()
                except Exception as re_err:
                    logger.warning(f"on_retry hook failed: {re_err!r}")


def _filename_from_url(url: str) -> str:
    """Derive a local filename from a URL.

    Strips the query string and returns the last path segment, falling back
    to ``"download.pdf"`` when the URL has no usable basename.
    """
    path = url.split("?", 1)[0]
    name = path.rsplit("/", 1)[-1]
    return name or "download.pdf"


def _download_one(
    page: Page,
    context: BrowserContext,
    idx: int,
    info: dict,
    dest_dir: str,
) -> None:
    """Download a single file described by ``info`` into ``dest_dir``.

    Two code paths depending on the link type:

    * ``javascript:`` hrefs are session-tied: the corresponding anchor is
      re-queried fresh by index and clicked inside a ``page.expect_download``
      block so Playwright captures the resulting download event.
    * Plain URLs are fetched through ``context.request`` so the browser's
      session cookies carry over without opening a new tab.

    Timeouts during the download are logged as warnings rather than raised —
    losing one file must not abort the rest of the batch.
    """
    href = info.get("href", "") or ""
    data_href = info.get("data_href", "") or ""
    text = info.get("text", "")
    logger.debug(f"Downloading: {text} - href: {href} - data-href: {data_href}")

    try:
        if "javascript" in href:
            # Session-tied JET download — must be triggered by the click handler.
            # Re-query the locator right before click so the index points at the
            # current DOM state, avoiding any re-render race.
            with page.expect_download(timeout=120000) as dl_info:
                page.locator("a[data-oce-meta-data], a[data-ucm-meta-data]").nth(
                    idx
                ).click()
            download = dl_info.value
            target = os.path.join(dest_dir, download.suggested_filename)
            download.save_as(target)
            logger.debug(f"Saved: {target}")
        else:
            # Plain URL — reuse browser session cookies via the API request context
            response = context.request.get(href)
            if not response.ok:
                logger.warning(f"Failed to download {href}: HTTP {response.status}")
                return
            filename = _filename_from_url(href)
            target = os.path.join(dest_dir, filename)
            with open(target, "wb") as f:
                f.write(response.body())
            logger.debug(f"Saved: {target}")
    except PlaywrightTimeoutError as e:
        logger.warning(f"Download did not trigger for {text!r}: {e}")


def _launch_browser(p, headed: bool):
    """Launch Firefox with the project's standard options. Small helper shared by
    the bootstrap login browser and each worker browser."""
    return p.firefox.launch(
        headless=not headed,
        slow_mo=300 if headed else 0,
        firefox_user_prefs={"pdfjs.disabled": True},
    )


def _new_context(browser, storage_state: dict | None = None):
    """Create a download-enabled context, optionally hydrated with ``storage_state``."""
    kwargs = {
        "viewport": {"width": 1280, "height": 900},
        "accept_downloads": True,
    }
    if storage_state is not None:
        kwargs["storage_state"] = storage_state
    return browser.new_context(**kwargs)


def _download_source(page: Page, context: BrowserContext, source: dict) -> None:
    """Drive ``page`` through a single source and save the files it exposes.

    Caller owns the browser/context lifecycle. Timeouts on one source are
    logged and swallowed so the surrounding worker loop can move on to the
    next one.
    """
    try:
        _goto_doc(page, source["doc_id"])

        # Best-effort check that we landed on the doc page.
        try:
            page.get_by_role(
                "heading",
                name=re.compile(source.get("desc", ""), re.I),
            ).wait_for(timeout=25000)
        except PlaywrightTimeoutError:
            logger.warning(
                f"Heading matching {source.get('desc')!r} not "
                f"detected — continuing anyway"
            )

        links = execute_with_retry(
            lambda s=source: _collect_links(page, s["doc_id"]),
            retries=3,
            on_retry=lambda s=source: _goto_doc(page, s["doc_id"]),
        )

        logger.debug("Start downloading...")
        for idx, info in enumerate(
            progressbar.track(
                links,
                description=(f"Downloading files from docid {source['doc_id']}"),
            )
        ):
            _download_one(page, context, idx, info, file_path)

    except PlaywrightTimeoutError as e:
        logger.error(f"Timeout for source {source['doc_id']}: {e!r} — skipping")


def _bootstrap_storage_state(headed: bool) -> dict | None:
    """Run the SSO flow once and return a ``storage_state`` dict.

    A minimal one-off browser: log in, grab the cookies + localStorage the
    worker threads need, then close. Returns ``None`` if anything in the
    login path fails (missing credentials, Playwright error, etc.).
    """
    with sync_playwright() as p:
        browser = None
        try:
            logger.debug("Launching bootstrap Firefox for login")
            browser = _launch_browser(p, headed)
            context = _new_context(browser)
            page = context.new_page()
            page.set_default_timeout(60000)

            if not _login(page):
                return None
            return context.storage_state()
        except Exception as e:
            logger.exception(f"{type(e).__name__}: {e!r}")
            return None
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    logger.warning(f"Error closing bootstrap browser: {e}")


def _worker_download(
    source_queue: "queue.Queue[dict]",
    storage_state: dict,
    headed: bool,
    worker_result: list,
    worker_id: int,
) -> None:
    """Auth worker thread body: own Playwright + browser, shared queue.

    Each worker launches its own browser hydrated with the shared
    ``storage_state`` (no re-login) and then pulls the next pending source
    from ``source_queue`` until it is empty. Work is dispatched on demand,
    so fast workers pick up more docs while slow ones finish their current
    one. On any fatal error ``worker_result[0]`` is flipped to ``False`` so
    the caller can fail the whole run cleanly.
    """
    with sync_playwright() as p:
        browser = None
        try:
            logger.debug(f"[worker-{worker_id}] launching Firefox")
            browser = _launch_browser(p, headed)
            context = _new_context(browser, storage_state=storage_state)
            page = context.new_page()
            page.set_default_timeout(60000)
            while True:
                try:
                    source = source_queue.get_nowait()
                except queue.Empty:
                    break
                _download_source(page, context, source)
        except Exception as e:
            logger.exception(f"[worker-{worker_id}] {type(e).__name__}: {e!r}")
            worker_result[0] = False
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    logger.warning(f"[worker-{worker_id}] error closing browser: {e}")


def download_docs(
    sources: list[dict[str, str]],
    headed: bool = False,
    result: list | None = None,
    login_done: threading.Event | None = None,
    workers: int = 2,
) -> None:
    """Log into MOS once, then download every source in parallel workers.

    Flow:

    1. A bootstrap browser runs the SSO + TOTP flow once to produce a
       ``storage_state`` (cookies + localStorage). It closes right after.
    2. ``login_done`` is fired as soon as step 1 finishes — successfully or
       not — so sibling threads (like the no-auth downloader) can move on.
    3. Every source is pushed onto a shared queue. ``workers`` threads each
       spin up their own Playwright + Firefox, hydrate the context with the
       shared ``storage_state`` (skipping the login), and pull the next
       pending source off the queue until it is empty. Work is dispatched
       on demand, so a slow doc does not stall the other workers.

    Because Playwright's sync handles cannot cross threads, every browser is
    created inside the thread that uses it. ``workers`` is clamped to
    ``[1, len(sources)]``; setting it to ``1`` reproduces the sequential
    behavior.

    ``result`` is an optional ``[bool]`` out-parameter that is flipped to
    ``False`` when the bootstrap login fails or any worker errors out.
    """
    # Reset work folder for a clean download
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)
    os.makedirs(file_path, exist_ok=True)

    if not sources:
        if login_done is not None:
            login_done.set()
        return

    try:
        storage_state = _bootstrap_storage_state(headed)
    finally:
        # Always release waiters, even if the bootstrap blew up.
        if login_done is not None and not login_done.is_set():
            login_done.set()

    if storage_state is None:
        if result is not None:
            result[0] = False
        return

    worker_count = max(1, min(workers, len(sources)))
    source_queue: queue.Queue = queue.Queue()
    for s in sources:
        source_queue.put(s)
    worker_results = [[True] for _ in range(worker_count)]

    threads = []
    for i in range(worker_count):
        t = threading.Thread(
            target=_worker_download,
            args=(source_queue, storage_state, headed, worker_results[i], i),
            name=f"auth-worker-{i}",
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    if result is not None and any(not r[0] for r in worker_results):
        result[0] = False


if __name__ == "__main__":
    download_docs(
        [
            {"desc": "Merch functional docs", "doc_id": "1585843.1"},
            {"desc": "Extensions docs--", "doc_id": "2978473.1"},
        ],
        headed=True,
    )
