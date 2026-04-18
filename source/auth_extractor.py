import os
import re
import shutil
import subprocess
from time import sleep

from dotenv import load_dotenv
from interface import logger, progressbar
from playwright.sync_api import BrowserContext, Page, sync_playwright
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


def _resolve_secret(value: str | None) -> str | None:
    """Resolve a credential value, transparently dereferencing 1Password refs.

    If ``value`` is a ``op://vault/item/field`` reference, shells out to the
    ``op`` CLI to read the secret. Plain strings (and ``None``) are returned
    as-is so the caller can mix stored secrets and inline values freely.
    """
    if value and value.startswith("op://"):
        result = subprocess.run(
            ["op", "read", value], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    return value


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

    logger.debug("Opening MOS sign-in page")
    page.goto("https://support.oracle.com/signin/", wait_until="domcontentloaded")

    logger.debug("Clicking 'Sign in with your commercial'")
    page.get_by_role("button", name="Sign in with your commercial").click()

    logger.debug("Submitting tenancy")
    page.get_by_role("textbox", name="Tenancy").fill("myoraclesupport")
    page.get_by_role("button", name="Continue").click()

    logger.debug("Selecting identity domain")
    page.locator('[data-test-id="identity-domain-dropdown"]').select_option(
        SSO_DOMAIN_OCID
    )
    page.get_by_role("button", name="Next").click()

    logger.debug("Submitting username")
    page.get_by_role("textbox", name="Username or email").fill(mos_user)
    page.get_by_role("button", name="Next").click()

    logger.debug("Submitting password")
    page.locator('[id="idcs-auth-pwd-input|input"]').fill(mos_pass)
    page.get_by_role("button", name="Sign In").click()

    logger.debug("Submitting 2fa")
    page.get_by_role("textbox", name="Passcode").fill(TOTP(mos_mfa_key).now())
    page.get_by_role("button", name="Verify").click()

    logger.debug("Waiting for post-login redirect")
    page.wait_for_url("**/support.oracle.com/**", timeout=60000)
    page.wait_for_load_state("domcontentloaded", timeout=30000)

    # Oracle fires a few more redirects right after the SSO handshake lands.
    sleep(5)
    return True


def _goto_doc(page: Page, doc_id: str) -> None:
    """Navigate to the ``DocumentDisplay`` page for the given MOS doc id.

    Uses the already-authenticated session on ``page`` and waits for the
    initial DOM plus a ``networkidle`` state so any JET/iframe assets finish
    loading before the caller starts collecting links.
    """
    logger.debug(f"Navigating to DocumentDisplay?id={doc_id}")
    page.goto(DOC_DISPLAY_URL + doc_id, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=45000)


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


def execute_with_retry(func, retries: int = 3):
    """Call ``func()`` up to ``retries`` times, retrying only on transient errors.

    Retries when :class:`NoValidLinksFound` is raised (Oracle's async rendering
    sometimes leaves the link list momentarily empty) and re-raises on the
    last attempt. Any other exception is propagated immediately — callers
    should not rely on this helper to swallow generic failures.
    """
    for retry in range(1, retries + 1):
        try:
            return func()
        except NoValidLinksFound as e:
            if logger:
                logger.warning(f"{e} — retrying ({retry}/{retries})")
            if retry == retries:
                raise
        except Exception:
            raise


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


def download_docs(
    sources: list[dict[str, str]],
    headed: bool = False,
    result: list | None = None,
) -> None:
    """Open Chromium via Playwright, log into MOS and download every source.

    Owns the full browser lifecycle: launches Chromium, creates a download-
    enabled context, logs in once, and then iterates ``sources`` — each
    entry is a dict with ``desc`` and ``doc_id`` keys — navigating to its
    ``DocumentDisplay`` page and downloading every attached file into the
    work folder.

    MUST run entirely on a single thread: Playwright's sync API binds to the
    thread where ``sync_playwright()`` is started and its handles cannot
    cross threads.

    ``headed=True`` shows the browser window and adds a small ``slow_mo``
    for debuggability. ``result`` is an optional single-element list used
    as an out-parameter — it is flipped to ``[False]`` whenever the run
    aborts early (missing credentials, unhandled exception) so callers can
    skip any downstream steps that depend on a successful download.
    """
    # Reset work folder for a clean download
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)
    os.makedirs(file_path, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        try:
            logger.debug("Launching Chromium via Playwright")
            browser = p.chromium.launch(
                headless=not headed,
                slow_mo=300 if headed else 0,
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                accept_downloads=True,
            )
            page = context.new_page()
            page.set_default_timeout(60000)

            if not _login(page):
                if result is not None:
                    result[0] = False
                return

            for source in sources:
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
                    )

                    logger.debug("Start downloading...")
                    for idx, info in enumerate(
                        progressbar.track(
                            links,
                            description=(
                                f"Downloading files from docid {source['doc_id']}"
                            ),
                        )
                    ):
                        _download_one(page, context, idx, info, file_path)

                except PlaywrightTimeoutError as e:
                    logger.error(
                        f"Timeout for source {source['doc_id']}: {e!r} — skipping"
                    )
                    continue

        except Exception as e:
            logger.exception(f"{type(e).__name__}: {e!r}")
            if result is not None:
                result[0] = False
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")


if __name__ == "__main__":
    download_docs(
        [
            {"desc": "Merch functional docs", "doc_id": "1585843.1"},
            {"desc": "Extensions docs--", "doc_id": "2978473.1"},
        ],
        headed=True,
    )
