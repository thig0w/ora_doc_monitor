import contextlib
import os
import shutil
import subprocess
import threading
from time import sleep
from urllib.parse import urlparse

from dotenv import load_dotenv
from interface import logger, progressbar
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pyotp import TOTP


class NoValidLinksFound(Exception):
    pass


load_dotenv()

# Module-level playwright instance — started in open_driver, stopped on cleanup
_pw = None

# Persistent base folder — always exists, never deleted
_base_path = os.path.join(os.getcwd(), "func_docs")
os.makedirs(_base_path, exist_ok=True)

# Working download folder — set at download time
file_path = os.path.join(os.getcwd(), "func_docs_work")


def _resolve_secret(value: str | None) -> str | None:
    """If value is a 1Password reference (op://...), resolve it via the op CLI."""
    if value and value.startswith("op://"):
        result = subprocess.run(
            ["op", "read", value], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    return value


def _goto(page: Page, url: str) -> None:
    """Navigate, tolerating NS_BINDING_ABORTED from Oracle MOS redirect chains.

    Oracle redirects /support/?kmContentId=… to /ic/builder/…, causing Firefox
    to abort the original request. The page lands at the correct URL; subsequent
    _wait_for_jet() calls confirm readiness.
    """
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        if "NS_BINDING_ABORTED" not in str(e):
            raise
        logger.debug(f"Navigation redirect absorbed for {url}")
        # Redirect is still in flight — wait for the destination to load
        page.wait_for_load_state("domcontentloaded")


def _wait_for_jet(page: Page) -> None:
    """Wait for Oracle JET framework to be fully ready."""
    page.wait_for_function(
        "window.oj && oj.Context.getPageContext().getBusyContext().isReady()"
    )
    page.locator("oj-vb-content.oj-complete").first.wait_for()


def _filename_from_response(response, href: str) -> str:
    """Extract filename from Content-Disposition header or URL path."""
    content_disp = response.headers.get("content-disposition", "")
    if "filename=" in content_disp:
        fname = content_disp.split("filename=")[-1].strip().strip("\"'")
        if fname:
            return fname
    path = urlparse(href).path
    return path.split("/")[-1] or "download.pdf"


def _cleanup_playwright() -> None:
    global _pw
    if _pw is not None:
        with contextlib.suppress(Exception):
            _pw.stop()
        _pw = None


def watchdog():
    logger.critical("Watchdog expired — forcing exit.")
    _cleanup_playwright()
    os._exit(1)


def open_driver(headed: bool = False) -> Page | None:
    global _pw
    logger.debug("Creating Playwright browser instance")
    mos_user = _resolve_secret(os.getenv("MOSUSER"))
    mos_pass = _resolve_secret(os.getenv("MOSPASS"))
    mos_mfa_key = _resolve_secret(os.getenv("MOSMFAKEY")).replace(" ", "")

    if mos_user is None or mos_pass is None:
        logger.critical("Please set MOSUSER and MOSPASS environment variables!")
        return None

    try:
        _pw = sync_playwright().start()
        browser = _pw.firefox.launch(headless=not headed)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)

        logger.debug("Opening Login Page")
        _goto(page, "https://support.oracle.com/support/?kmContentId=1585843")

        # Wait for page readiness sentinel, then Oracle JET framework
        logger.debug("Clicking Sign In button")
        page.locator("#mc-id-other-sign-in-btn").wait_for(state="visible")
        _wait_for_jet(page)

        page.locator("#mc-id-sign-in-with-commercial-cloud-account-btn").click()

        logger.debug("Submitting tenant")
        page.locator("#tenant").fill("myoraclesupport")
        page.keyboard.press("Enter")

        logger.debug("Selecting sso-domain")
        page.locator("[data-test-id='identity-domain-dropdown']").select_option(
            label="sso-domain"
        )
        page.locator("#submit-domain").click()

        logger.debug("Submitting username")
        page.locator("#idcs-signin-basic-signin-form-username").fill(mos_user)
        page.keyboard.press("Enter")

        logger.debug("Submitting password")
        # IDs containing | must use attribute selectors (| is a CSS namespace separator)
        page.locator('[id="idcs-auth-pwd-input|input"]').fill(mos_pass)
        page.keyboard.press("Enter")

        logger.debug("Submitting 2fa")
        page.locator('[id="idcs-mfa-mfa-auth-passcode-input|input"]').fill(
            TOTP(mos_mfa_key).now()
        )
        page.keyboard.press("Enter")

        logger.debug("Waiting to fully load")
        _wait_for_jet(page)

        return page

    except Exception as e:
        logger.error(f"Error trying to open browser and login: {e}")
        _cleanup_playwright()
        return None


def download_docs(
    sources: list[dict[str, str]], page: Page = None, result: list | None = None
):
    # Reset work folder for a clean download
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)
    os.makedirs(file_path, exist_ok=True)

    base_url = "https://support.oracle.com/support/?kmContentId="
    try:
        if page is None:
            page = open_driver()

        for source in sources:
            try:
                _goto(page, base_url + source["doc_id"].split(".")[0])
                logger.debug("Waiting first element to load...")

                href_links = execute_with_retry(
                    lambda: load_page_and_collect_links(
                        page,  # noqa: B023
                        source["doc_id"],  # noqa: B023
                    ),
                    retries=3,
                )

                logger.debug("Start downloading...")

                for idx, i in enumerate(
                    progressbar.track(
                        href_links,
                        description=f"Downloading files from docid {source['doc_id']}",
                    )
                ):
                    href = i.get("href", "") or ""
                    data_href = i.get("data_href", "") or ""
                    text = i.get("text", "")
                    logger.debug(
                        f"Downloading: {text} - href: {href} - data-href: {data_href}"
                    )
                    if "javascript" in href:
                        # Lazy locator re-queries DOM on each call — no stale risk.
                        # Token is session-tied; must go through JET click handler.
                        with page.expect_download(timeout=60000) as dl_info:
                            page.locator(
                                "a[data-oce-meta-data], a[data-ucm-meta-data]"
                            ).nth(idx).click()
                        dl = dl_info.value
                        dl.save_as(os.path.join(file_path, dl.suggested_filename))
                    else:
                        # Direct URL — fetch via browser session to inherit auth cookies
                        response = page.request.get(href)
                        filename = _filename_from_response(response, href)
                        with open(os.path.join(file_path, filename), "wb") as f:
                            f.write(response.body())
                    sleep(0.5)

            except PlaywrightTimeoutError as e:
                logger.error(
                    f"TimeoutError for source {source['doc_id']}: {e!r} — skipping"
                )
                continue

    except Exception as e:
        logger.exception(f"{type(e).__name__}: {e!r}")

    finally:
        alarm = threading.Timer(interval=30, function=watchdog)
        alarm.start()
        try:
            if page is not None:
                page.context.browser.close()
        except Exception as e:
            logger.error(f"Error closing browser: {e}")
        _cleanup_playwright()
        alarm.cancel()


def load_page_and_collect_links(page: Page, source: str) -> list[dict]:
    page.locator(".oj-sp-item-overview-page-main-strip").wait_for()

    logger.debug("Waiting for the page to fully load...")
    _wait_for_jet(page)

    # Wait until link elements are present and at least one has a non-empty href
    page.wait_for_function("""
        () => {
            const elems = document.querySelectorAll(
                'a[data-oce-meta-data], a[data-ucm-meta-data]'
            );
            return elems.length > 0 &&
                   Array.from(elems).some(el => {
                       const h = el.getAttribute('href');
                       return h && h.trim().length > 0;
                   });
        }
    """)

    # Extract all attributes atomically in one JS call before DOM can re-render
    elements = page.query_selector_all("a[data-oce-meta-data], a[data-ucm-meta-data]")
    link_data = page.evaluate(
        """(elems) => elems.map(el => ({
            href: el.getAttribute('href'),
            data_href: el.getAttribute('data-href'),
            text: el.textContent.trim()
        }))""",
        elements,
    )

    valid_links = [d for d in link_data if d.get("href")]
    if not valid_links:
        raise NoValidLinksFound(f"No links found for {source}")

    return link_data


def execute_with_retry(func, retries=3):
    for retry in range(1, retries + 1):
        try:
            return func()
        except NoValidLinksFound as e:
            logger.warning(f"{e} — retrying ({retry}/{retries})")
            if retry == retries:
                raise
        except Exception:
            # unexpected exception → fail fast
            raise


if __name__ == "__main__":
    driver = open_driver(True)
    if driver is not None:
        # merch doc lib
        download_docs(
            [
                {"desc": "Merch functional docs", "doc_id": "1585843.1"},
                {"desc": "Extensions docs--", "doc_id": "2978473.1"},
                # {"desc": "Rics func docs", "doc_id": "2643542.1"},
                # {"desc": "RDS func docs", "doc_id": "2899701.1"},
                # {"desc": "RDS func docs", "doc_id": "2899701.1"},
                # {"desc": "POM func docs", "doc_id": "2815461.1"},
                # {"desc": "Locatization func docs (KA903) refactor",
                #  "doc_id": "2534504.2"},
                # {"desc": "blueprint func docs", "doc_id": "2677553.1"},
            ],
            driver,
        )
