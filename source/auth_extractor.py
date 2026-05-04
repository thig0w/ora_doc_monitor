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


def _wid() -> str:
    """Return ``[worker-N] `` when called from an auth-worker thread, else ``""``.

    Threads spawned by :func:`download_docs` are named ``auth-worker-N``, so
    helper functions reachable from a worker can tag their log lines with the
    same ``[worker-N]`` prefix used directly inside :func:`_worker_download`
    without having to thread ``worker_id`` through every signature.
    """
    name = threading.current_thread().name
    if name.startswith("auth-worker-"):
        return f"[worker-{name.removeprefix('auth-worker-')}] "
    return ""


def _resolve_secrets(
    values: dict[str, str | None], retries: int = 3, backoff: float = 1.5
) -> dict[str, str | None]:
    """Resolve a dict of credential values, batching 1Password refs into one call.

    Entries whose value is ``None`` or not an ``op://vault/item/field``
    reference pass through untouched. All ``op://`` refs in ``values`` are
    collected and resolved in a single ``op inject`` subprocess call, so the
    cost of talking to 1Password (session re-auth, biometric prompt, network
    round-trip) is paid once per batch instead of once per variable.

    The template sent to ``op inject`` is a ``.env``-style file — one
    ``KEY={{ op://... }}`` line per ref — which is the canonical 1Password
    template format. The response is parsed line-by-line, splitting on the
    first ``=``; this handles values that themselves contain ``=`` (e.g. a
    password with that character) without corrupting them.

    Values are assumed to be single-line (true for Oracle SSO credentials
    and Base32 TOTP keys). A multi-line secret would break the line split,
    so this helper should not be used for PEM keys or similar.

    The ``op`` CLI fails intermittently (session re-auth, biometric timeout,
    throttling), so non-zero exits are retried with linear backoff. The CLI's
    stderr is logged on every failure so a persistent error is visible before
    the final raise.
    """
    op_refs = {k: v for k, v in values.items() if v and v.startswith("op://")}
    resolved: dict[str, str | None] = {
        k: v for k, v in values.items() if k not in op_refs
    }
    if not op_refs:
        return resolved

    keys = list(op_refs.keys())
    template = "\n".join(f"{k}={{{{ {op_refs[k]} }}}}" for k in keys)

    last_err: subprocess.CalledProcessError | None = None
    for attempt in range(1, retries + 1):
        logger.debug(f"{_wid()}Opening 1pass subprocess for {keys}")
        result = subprocess.run(
            ["op", "inject"],
            input=template,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            parsed: dict[str, str] = {}
            for line in result.stdout.splitlines():
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k in op_refs:
                    parsed[k] = v
            missing = [k for k in keys if k not in parsed]
            if missing:
                raise RuntimeError(
                    f"op inject did not return values for {missing} — "
                    f"template/output mismatch"
                )
            resolved.update(parsed)
            return resolved

        stderr = (result.stderr or "").strip()
        logger.warning(
            f"{_wid()}op inject failed for {keys} (attempt {attempt}/{retries}, "
            f"exit {result.returncode}): {stderr or '<no stderr>'}"
        )
        last_err = subprocess.CalledProcessError(
            result.returncode,
            ["op", "inject"],
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
    # Kick off the sign-in page navigation first with an early-return
    # ``wait_until="commit"`` — it unblocks as soon as the first bytes land,
    # leaving the browser to render the page in the background while we go
    # talk to 1Password. The subsequent ``wait_for(state="visible")`` on the
    # sign-in button absorbs whatever render time is left, so there's no race.
    logger.debug(f"{_wid()}Opening MOS sign-in page")
    page.goto("https://support.oracle.com/signin/", wait_until="commit")

    logger.debug(f"{_wid()}Resolving secrets")
    secrets = _resolve_secrets(
        {
            "MOSUSER": os.getenv("MOSUSER"),
            "MOSPASS": os.getenv("MOSPASS"),
            "MOSMFAKEY": os.getenv("MOSMFAKEY"),
        }
    )
    mos_user = secrets["MOSUSER"]
    mos_pass = secrets["MOSPASS"]
    mos_mfa_raw = secrets["MOSMFAKEY"]

    if mos_user is None or mos_pass is None or mos_mfa_raw is None:
        logger.critical(
            f"{_wid()}Please set MOSUSER, MOSPASS and MOSMFAKEY environment variables!"
        )
        return False

    mos_mfa_key = mos_mfa_raw.replace(" ", "")

    step_timeout = 45000

    # Each screen transition is guarded by a wait_for on the next screen's
    # distinctive element. JET does SPA-style view switches without URL
    # changes, so expect_navigation is not reliable — explicit element
    # waits are the barrier that actually matters here.
    sign_in_btn = page.get_by_role("button", name="Sign in with your commercial")
    sign_in_btn.wait_for(state="visible", timeout=step_timeout)
    logger.debug(f"{_wid()}Clicking 'Sign in with your commercial'")
    sign_in_btn.click()

    tenancy_field = page.get_by_role("textbox", name="Tenancy")
    tenancy_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug(f"{_wid()}Submitting tenancy")
    tenancy_field.fill("myoraclesupport")
    page.get_by_role("button", name="Continue").click()

    domain_dropdown = page.locator('[data-test-id="identity-domain-dropdown"]')
    domain_dropdown.wait_for(state="visible", timeout=step_timeout)
    logger.debug(f"{_wid()}Selecting identity domain")
    domain_dropdown.select_option(SSO_DOMAIN_OCID)
    page.get_by_role("button", name="Next").click()

    username_field = page.get_by_role("textbox", name="Username or email")
    username_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug(f"{_wid()}Submitting username")
    username_field.fill(mos_user)
    page.get_by_role("button", name="Next").click()

    pwd_field = page.locator('[id="idcs-auth-pwd-input|input"]')
    pwd_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug(f"{_wid()}Submitting password")
    pwd_field.fill(mos_pass)
    page.get_by_role("button", name="Sign In").click()

    passcode_field = page.get_by_role("textbox", name="Passcode")
    passcode_field.wait_for(state="visible", timeout=step_timeout)
    logger.debug(f"{_wid()}Submitting 2fa")
    passcode_field.fill(TOTP(mos_mfa_key).now())
    page.get_by_role("button", name="Verify").click()

    logger.debug(f"{_wid()}Waiting for post-login redirect")
    page.wait_for_url("**/support.oracle.com/**", timeout=60000)

    # The post-login redirect cascade is long and `networkidle` fires on any
    # 500 ms lull, so it can return mid-cascade — before MOS has finished
    # planting every session cookie. Worker-0 keeps using its live context
    # and silently picks up the late cookies, but the storage_state snapshot
    # we share with other workers is captured right after this wait, so any
    # cookie that arrives late is missing from it and hydrating workers get
    # bounced to /signin/ on their first /epmos/ request.
    #
    # Instead, wait for an element that only renders once the final landing
    # page (https://support.oracle.com/support/) has fully mounted: the
    # account menu button. When it is visible, the cascade is genuinely
    # done and storage_state is safe to capture.
    logger.debug(f"{_wid()}Waiting for MOS landing account menu button")
    try:
        page.locator("#mc-id-sptemplate-account-menu-btn").wait_for(
            state="visible", timeout=120000
        )
    except PlaywrightTimeoutError as e:
        logger.warning(
            f"{_wid()}Account menu button not visible after login "
            f"({page.url}): {e} — continuing"
        )

    # Eager sanity check: if MOS bounced us back to the welcome page, the SSO
    # didn't actually stick. Failing here keeps a half-authenticated
    # storage_state from being shared with the other workers.
    if "/signin" in page.url or "login.oracle.com" in page.url:
        logger.error(
            f"{_wid()}Post-login URL still on auth page: {page.url} — "
            "treating as login failure"
        )
        return False
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
    logger.debug(f"{_wid()}Navigating to DocumentDisplay?id={doc_id}")
    try:
        page.goto(target, wait_until="commit", timeout=60000)
    except PlaywrightError as e:
        if "NS_BINDING_ABORTED" in str(e):
            logger.debug(f"{_wid()}Goto aborted by Oracle redirect — continuing: {e}")
        else:
            raise
    try:
        page.wait_for_load_state("networkidle", timeout=45000)
    except PlaywrightTimeoutError as e:
        logger.warning(f"{_wid()}networkidle timed out for {doc_id}: {e} — continuing")


def _wait_for_jet_ready(page: Page, source_id: str) -> None:
    """Block until Oracle JET finished rendering the doc page's content.

    JET ships two first-class readiness signals that fire strictly after
    ``networkidle`` — waiting on them is the proven way (copied from the
    Selenium version) to avoid catching the DOM mid-render:

    1. ``oj.Context.getPageContext().getBusyContext().isReady()`` — JET's
       own busy context flips to ready once all its async operations
       (data binding, component bootstrap, deferred fetches) have settled.
    2. ``oj-vb-content.oj-complete`` — the Virtual Builder content subtree
       picks up the ``oj-complete`` CSS class only after its children are
       fully mounted. Some doc pages don't use ``oj-vb-content`` at all, so
       this wait is best-effort.
    """
    logger.debug(f"{_wid()}Waiting for Oracle JET BusyContext to be ready...")
    try:
        page.wait_for_function(
            """() => window.oj
                && oj.Context.getPageContext().getBusyContext().isReady()""",
            timeout=60000,
        )
    except PlaywrightTimeoutError as e:
        logger.warning(
            f"{_wid()}JET BusyContext not ready for {source_id}: {e} — continuing"
        )

    logger.debug(
        f"{_wid()}Waiting for JET content subtree (oj-vb-content.oj-complete)..."
    )
    try:
        page.locator("oj-vb-content.oj-complete").first.wait_for(
            state="attached", timeout=45000
        )
    except PlaywrightTimeoutError:
        logger.debug(
            f"{_wid()}oj-vb-content.oj-complete not seen for {source_id} — "
            f"page may not use it, continuing"
        )


def _collect_links(page: Page, source_id: str) -> list[dict]:
    """Collect every downloadable link on the current MOS doc page.

    Blocks first on :func:`_wait_for_jet_ready` so Oracle JET has committed
    to its final DOM, then waits until the
    ``a[data-oce-meta-data], a[data-ucm-meta-data]`` anchors have non-empty
    ``href`` values, then extracts ``href``, ``data_href`` and text for each
    one in a single ``page.evaluate`` call so the DOM cannot re-render
    between individual reads.

    Raises :class:`NoValidLinksFound` if, after the wait, no anchor has a
    usable ``href`` — callers retry this case via :func:`execute_with_retry`.
    Returns the raw list of dicts (one per anchor).
    """
    _wait_for_jet_ready(page, source_id)

    logger.debug(f"{_wid()}Waiting for document links to populate...")
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
            logger.warning(f"{_wid()}{e} — retrying ({retry}/{retries})")
            if retry == retries:
                raise
            if on_retry is not None:
                try:
                    on_retry()
                except Exception as re_err:
                    logger.warning(f"{_wid()}on_retry hook failed: {re_err!r}")


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
    logger.debug(f"{_wid()}Downloading: {text} - href: {href} - data-href: {data_href}")

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
            logger.debug(f"{_wid()}Saved: {target}")
        else:
            # Plain URL — reuse browser session cookies via the API request context
            response = context.request.get(href)
            if not response.ok:
                logger.warning(
                    f"{_wid()}Failed to download {href}: HTTP {response.status}"
                )
                return
            filename = _filename_from_url(href)
            target = os.path.join(dest_dir, filename)
            with open(target, "wb") as f:
                f.write(response.body())
            logger.debug(f"{_wid()}Saved: {target}")
    except PlaywrightTimeoutError as e:
        logger.warning(f"{_wid()}Download did not trigger for {text!r}: {e}")


def _launch_browser(p, headed: bool):
    """Launch Firefox with the project's standard options. Small helper shared by
    the bootstrap login browser and each worker browser."""
    return p.firefox.launch(
        headless=not headed,
        slow_mo=300 if headed else 0,
        firefox_user_prefs={"pdfjs.disabled": True},
    )


def _new_context(browser, storage_state: dict | None = None):
    """Create a download-enabled context, optionally hydrated with ``storage_state``.

    ``locale`` is set explicitly because Oracle's cloud sign-in JS reads
    ``navigator.language`` and stamps it into the ``Accept-Language`` header
    on the ``/v1/oauth2/authorize`` request. Without a locale, Firefox under
    Playwright leaves ``navigator.language`` undefined and Oracle's gateway
    rejects the request with HTTP 400, breaking the SSO redirect.
    """
    kwargs = {
        "viewport": {"width": 1280, "height": 900},
        "accept_downloads": True,
        "locale": "en-US",
    }
    if storage_state is not None:
        kwargs["storage_state"] = storage_state
    return browser.new_context(**kwargs)


def _load_doc_page(page: Page, source: dict) -> None:
    """Navigate to ``source`` and wait until the JET doc page has actually rendered.

    ``_goto_doc`` alone only guarantees ``networkidle``, which fires long
    before Oracle JET swaps the loading skeleton out for the real DOM — so
    reading anchors right after it often catches the page mid-render. Here
    we additionally wait for the doc's ``desc`` heading to become visible,
    which is a much later signal that the content container has mounted.
    The heading wait is best-effort: we log and continue if it times out so
    docs whose ``desc`` does not exactly match the rendered heading still
    progress.
    """
    _goto_doc(page, source["doc_id"])
    try:
        page.get_by_role(
            "heading",
            name=re.compile(source.get("desc", ""), re.I),
        ).wait_for(timeout=25000)
    except PlaywrightTimeoutError:
        logger.warning(
            f"{_wid()}Heading matching {source.get('desc')!r} not detected — "
            f"continuing anyway"
        )


def _download_source(page: Page, context: BrowserContext, source: dict) -> None:
    """Drive ``page`` through a single source and save the files it exposes.

    Caller owns the browser/context lifecycle. Timeouts on one source are
    logged and swallowed so the surrounding worker loop can move on to the
    next one.
    """
    try:
        _load_doc_page(page, source)

        links = execute_with_retry(
            lambda s=source: _collect_links(page, s["doc_id"]),
            retries=3,
            on_retry=lambda s=source: _load_doc_page(page, s),
        )

        logger.debug(f"{_wid()}Start downloading...")
        for idx, info in enumerate(
            progressbar.track(
                links,
                description=(f"Downloading files from docid {source['doc_id']}"),
            )
        ):
            _download_one(page, context, idx, info, file_path)

    except PlaywrightTimeoutError as e:
        logger.error(f"{_wid()}Timeout for source {source['doc_id']}: {e!r} — skipping")


def _worker_download(
    source_queue: "queue.Queue[dict]",
    login_state: dict,
    login_done_internal: threading.Event,
    external_login_done: threading.Event | None,
    headed: bool,
    worker_result: list,
    worker_id: int,
    is_login_worker: bool,
) -> None:
    """Auth worker thread body: own Playwright + browser, shared queue.

    Worker-0 (``is_login_worker=True``) runs the MOS SSO + TOTP flow itself
    in its own browser, publishes the resulting ``storage_state`` onto the
    shared ``login_state`` dict, and keeps that **same browser** open to
    drain the download queue — no second browser launch for login. Workers
    1..N-1 launch their browser immediately (overlapping the SSO
    handshake) then block on ``login_done_internal``; once login has
    finished they rehydrate a context with the published ``storage_state``
    and join the download loop.

    ``external_login_done`` is the CLI's noauth gate. Only worker-0 sets
    it, and it fires as soon as ``_login`` returns (success or failure),
    so the noauth downloader is released the moment SSO finishes — not
    after the auth download loop drains.

    On any fatal error ``worker_result[0]`` is flipped to ``False``. Login
    failures are signalled via ``login_state["success"]`` and do not flip
    the waiting workers' results — they exit cleanly.
    """
    with sync_playwright() as p:
        browser = None
        try:
            logger.debug(f"[worker-{worker_id}] launching Firefox")
            browser = _launch_browser(p, headed)

            if is_login_worker:
                context = _new_context(browser)
                page = context.new_page()
                page.set_default_timeout(60000)
                try:
                    ok = _login(page)
                    if ok:
                        login_state["storage_state"] = context.storage_state()
                        login_state["success"] = True
                except Exception as e:
                    logger.exception(
                        f"[worker-{worker_id}] login failed: {type(e).__name__}: {e!r}"
                    )
                finally:
                    # Release waiters unconditionally — on success, failure,
                    # or exception — or sibling workers and the noauth
                    # thread hang forever.
                    login_done_internal.set()
                    if (
                        external_login_done is not None
                        and not external_login_done.is_set()
                    ):
                        external_login_done.set()
                if not login_state["success"]:
                    worker_result[0] = False
                    return
            else:
                login_done_internal.wait()
                if not login_state["success"]:
                    # Login failed in worker-0; exit cleanly without
                    # flipping our own result flag.
                    return
                context = _new_context(
                    browser, storage_state=login_state["storage_state"]
                )
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
            # If worker-0 blew up before the login `finally` ran (e.g.
            # browser launch itself failed), other workers would deadlock.
            # Release them now.
            if is_login_worker and not login_done_internal.is_set():
                login_done_internal.set()
                if external_login_done is not None and not external_login_done.is_set():
                    external_login_done.set()
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

    1. Every source is pushed onto a shared queue.
    2. ``worker_count`` threads spin up their own Playwright + Firefox in
       parallel. Worker-0 runs the SSO + TOTP flow in its browser and
       publishes the resulting ``storage_state`` on a shared dict; its
       browser stays open and is reused to process the queue. Workers
       1..N-1 launch their browser during login (overlapping the SSO
       handshake), then hydrate a context with the shared state once
       login finishes and join the queue. All workers pull the next
       pending source on demand, so a slow doc does not stall the others.
    3. ``login_done`` fires as soon as the SSO flow in worker-0 finishes
       (success or failure), releasing the sibling noauth thread right
       away instead of waiting for the download loop to drain.

    Because Playwright's sync handles cannot cross threads, every browser is
    created inside the thread that uses it. ``workers`` is clamped to
    ``[1, len(sources)]``; setting it to ``1`` reproduces the sequential
    behavior (single browser, login + downloads serially).

    ``result`` is an optional ``[bool]`` out-parameter that is flipped to
    ``False`` when login fails or any worker errors out.
    """
    # Reset work folder for a clean download
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)
    os.makedirs(file_path, exist_ok=True)

    if not sources:
        if login_done is not None:
            login_done.set()
        return

    login_state: dict = {"storage_state": None, "success": False}
    login_done_internal = threading.Event()

    worker_count = max(1, min(workers, len(sources)))
    source_queue: queue.Queue = queue.Queue()
    for s in sources:
        source_queue.put(s)
    worker_results = [[True] for _ in range(worker_count)]

    threads = []
    for i in range(worker_count):
        t = threading.Thread(
            target=_worker_download,
            args=(
                source_queue,
                login_state,
                login_done_internal,
                login_done if i == 0 else None,
                headed,
                worker_results[i],
                i,
                i == 0,
            ),
            name=f"auth-worker-{i}",
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    # Safety net: if worker-0 never started or crashed before the login
    # `finally` fired (extremely rare), release the external gate now so
    # the noauth thread is not left hanging.
    if login_done is not None and not login_done.is_set():
        login_done.set()

    if result is not None and (
        not login_state["success"] or any(not r[0] for r in worker_results)
    ):
        result[0] = False


if __name__ == "__main__":
    download_docs(
        [
            {"desc": "Merch functional docs", "doc_id": "1585843.1"},
            {"desc": "Extensions docs--", "doc_id": "2978473.1"},
        ],
        headed=True,
    )
