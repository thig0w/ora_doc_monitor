import glob
import os
import shutil
import subprocess
import threading
from time import sleep, time

import psutil
from dotenv import load_dotenv
from interface import logger, progressbar
from pyotp import TOTP
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import Select, WebDriverWait


class NoValidLinksFound(Exception):
    pass


load_dotenv()


def _resolve_secret(value: str | None) -> str | None:
    """If value is a 1Password reference (op://...), resolve it via the op CLI."""
    if value and value.startswith("op://"):
        result = subprocess.run(
            ["op", "read", value], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    return value


# Persistent base folder — always exists, never deleted
_base_path = os.path.join(os.getcwd(), "func_docs")
os.makedirs(_base_path, exist_ok=True)

# Working download folder — set at download time
file_path = os.path.join(os.getcwd(), "func_docs_work")


def watchdog():
    logger.critical("Watchdog expired. Exiting...")
    child_process = psutil.Process(os.getpid()).children(recursive=True)
    for process in child_process:
        logger.warning(f"Killing child process: {process.pid} - {process.name()}")
        try:
            process.kill()
        except psutil.NoSuchProcess as e:
            logger.warning(f"Error trying to terminate child process: {e}")
            continue


def count_files_with_extension(folder_path, extension):
    pattern = os.path.join(folder_path, f"*{extension}")
    files = glob.glob(pattern)
    return len(files)


# Wait downloads to finish
def wait_for_downloads(directory, timeout=10800, poll_interval=15):
    total = count_files_with_extension(directory, ".part")

    wait_bar = progressbar.add_task(
        "[cyan]Waiting for Downloads to finish...", total=total
    )

    end_time = time() + timeout
    while time() < end_time:
        partial = count_files_with_extension(directory, ".part")
        progressbar.update(wait_bar, completed=max(total - partial, 0))
        if partial == 0:
            return True
        sleep(poll_interval)

    raise TimeoutError("Downloads were not concluded during the specified time.")


def open_driver(headed: bool = False) -> webdriver:
    logger.debug("Creating webdriver instance")
    # Get user/pass — values may be plain strings or 1Password references (op://...)
    mos_user = _resolve_secret(os.getenv("MOSUSER"))
    mos_pass = _resolve_secret(os.getenv("MOSPASS"))
    mos_mfa_key = _resolve_secret(os.getenv("MOSMFAKEY")).replace(" ", "")

    # Init WebDriver options
    logger.debug("setting firefox options")
    firefox_options = Options()
    firefox_options.set_preference("browser.download.dir", file_path)
    firefox_options.set_preference("browser.download.folderList", 2)
    firefox_options.set_preference("browser.download.manager.showWhenStarting", False)
    firefox_options.set_preference(
        "browser.helperApps.neverAsk.saveToDisk",
        "application/octet-stream,application/pdf",
    )
    firefox_options.set_preference("pdfjs.disabled", True)
    if not headed:
        firefox_options.add_argument("--headless")
    else:
        # Disable background throttling
        firefox_options.set_preference("dom.min_background_timeout_value", 0)
        firefox_options.set_preference("dom.min_timeout_value", 0)
        firefox_options.set_preference(
            "dom.timeout.enable_budget_timer_throttling", False
        )
        firefox_options.set_preference(
            "browser.tabs.remote.useOcclusionTracking", False
        )
        firefox_options.set_preference(
            "browser.tabs.remote.useWindowOcclusionTracking", False
        )
        firefox_options.set_preference("network.http.throttle.enable", False)

    if mos_user is None or mos_pass is None:
        logger.critical("Please set MOSUSER and MOSPASS environment variables!")
        return None

    try:
        driver = webdriver.Firefox(options=firefox_options)

        # Open the Login Page
        logger.debug("Opening Login Page")
        url = "https://support.oracle.com/support/?kmContentId=1585843"
        driver.get(url)

        # Setting Driver Wait
        logger.debug("Setting webdriver timeout")
        wait = WebDriverWait(
            driver, 60, ignored_exceptions=[StaleElementReferenceException]
        )

        # Find and click the Sign In button
        logger.debug("Clicking Sign In button")
        wait.until(ec.element_to_be_clickable((By.ID, "mc-id-other-sign-in-btn")))

        # 1. JET logic ready
        wait.until(
            lambda d: d.execute_script("""
                           return window.oj &&
                                  oj.Context.getPageContext()
                                    .getBusyContext()
                                    .isReady();
                       """)
        )

        # 2. JET subtree visible
        wait.until(
            lambda d: (
                "oj-complete"
                in d.find_element(By.CSS_SELECTOR, "oj-vb-content").get_attribute(
                    "class"
                )
            )
        )

        sign_in_button = wait.until(
            ec.visibility_of_element_located(
                (By.ID, "mc-id-sign-in-with-commercial-cloud-account-btn")
            )
        )
        sign_in_button.click()

        # Find tenant textbox
        logger.debug("Submitting tenant")
        tenant_field = wait.until(ec.visibility_of_element_located((By.ID, "tenant")))
        tenant_field.send_keys("myoraclesupport")
        tenant_field.send_keys(Keys.RETURN)

        # Find selector
        logger.debug("selecting sso-domain")
        selector_field = wait.until(
            ec.visibility_of_element_located(
                (By.CSS_SELECTOR, "[data-test-id='identity-domain-dropdown']")
            )
        )
        select = Select(selector_field)
        select.select_by_visible_text("sso-domain")

        # Find next Button
        next_button = wait.until(
            ec.visibility_of_element_located((By.ID, "submit-domain"))
        )
        next_button.click()

        # Find and fill username
        logger.debug("Submitting username")
        username_field = wait.until(
            ec.visibility_of_element_located(
                (By.ID, "idcs-signin-basic-signin-form-username")
            )
        )
        username_field.send_keys(mos_user)
        username_field.send_keys(Keys.RETURN)

        # Find and fill the Password
        logger.debug("Submitting password")
        password_field = wait.until(
            ec.visibility_of_element_located((By.ID, "idcs-auth-pwd-input|input"))
        )
        password_field.send_keys(mos_pass)
        password_field.send_keys(Keys.RETURN)

        # Find and fill the 2fa
        logger.debug("Submitting 2fa")
        mfa_field = wait.until(
            ec.visibility_of_element_located(
                (By.ID, "idcs-mfa-mfa-auth-passcode-input|input")
            )
        )
        mfa_field.send_keys(TOTP(mos_mfa_key).now())
        mfa_field.send_keys(Keys.RETURN)

        # Must wait auth
        logger.debug("Waitting to fully load")
        # Best practice for Oracle JET
        # 1. JET logic ready
        wait.until(
            lambda d: d.execute_script("""
                   return window.oj &&
                          oj.Context.getPageContext()
                            .getBusyContext()
                            .isReady();
               """)
        )

        # 2. JET subtree visible
        wait.until(
            lambda d: (
                "oj-complete"
                in d.find_element(By.CSS_SELECTOR, "oj-vb-content").get_attribute(
                    "class"
                )
            )
        )

        return driver
    except Exception as e:
        logger.error(f"Error trying to Open webdriver and login: {e}")
    else:
        profile_dir = driver.capabilities.get("moz:profile")
        driver.quit()
        if profile_dir and os.path.isdir(profile_dir):
            shutil.rmtree(profile_dir, ignore_errors=True)

    return None


def download_docs(
    sources: list[dict[str, str]], driver: webdriver = None, result: list | None = None
):
    # Reset work folder for a clean download
    if os.path.isdir(file_path):
        shutil.rmtree(file_path)
    os.makedirs(file_path, exist_ok=True)

    base_url = "https://support.oracle.com/support/?kmContentId="
    try:
        if driver is None:
            driver = open_driver()

        for source in sources:
            try:
                driver.get(base_url + source["doc_id"].split(".")[0])

                # Wait page load
                logger.debug("Waiting first element to load...")
                # Setting Driver Wait
                wait = WebDriverWait(driver, 60)

                href_links = execute_with_retry(
                    lambda: load_page_and_collect_links(
                        wait,  # noqa: B023
                        source["doc_id"],  # noqa: B023
                        driver,  # noqa: B023
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
                        # Re-find element fresh immediately before click to avoid
                        # stale reference.
                        # The download token is session-tied and only triggered via
                        # the JET click handler.
                        fresh = driver.find_elements(
                            By.CSS_SELECTOR,
                            "a[data-oce-meta-data], a[data-ucm-meta-data]",
                        )
                        if idx < len(fresh):
                            fresh[idx].click()
                    else:
                        driver.execute_script(f"window.open('{href}')")
                    sleep(0.5)

                files = os.listdir(file_path)
                wait.until(lambda d: any(f.endswith(".pdf") for f in files))  # noqa: B023

            except TimeoutException as e:
                logger.error(
                    f"TimeoutException for source {source['doc_id']}: {e!r} — skipping"
                )
                continue

    except Exception as e:
        logger.exception(f"{type(e).__name__}: {e!r}")

    finally:
        try:
            wait_for_downloads(file_path)
        except TimeoutError as e:
            logger.error(f"Download wait timed out: {e}")
            if result is not None:
                result[0] = False
        alarm = threading.Timer(interval=4, function=watchdog)
        alarm.start()
        if driver is not None:
            profile_dir = driver.capabilities.get("moz:profile")
            driver.quit()
            if profile_dir and os.path.isdir(profile_dir):
                shutil.rmtree(profile_dir, ignore_errors=True)
                logger.debug(f"Removed Firefox temp profile: {profile_dir}")
        alarm.cancel()


def load_page_and_collect_links(wait, source, driver):
    wait.until(
        ec.visibility_of_element_located(
            (By.CLASS_NAME, "oj-sp-item-overview-page-main-strip")
        )
    )

    logger.debug("Waiting for the page to fully load...")
    # Best practice for Oracle JET
    # 1. JET logic ready
    wait.until(
        lambda d: d.execute_script("""
           return window.oj &&
                  oj.Context.getPageContext()
                    .getBusyContext()
                    .isReady();
       """)
    )

    # 2. JET subtree visible
    wait.until(
        lambda d: (
            "oj-complete"
            in d.find_element(By.CSS_SELECTOR, "oj-vb-content").get_attribute("class")
        )
    )

    # 3. Links are ready
    elems = wait.until(
        lambda d: anchors_have_href(
            d.find_elements(
                By.CSS_SELECTOR, "a[data-oce-meta-data], a[data-ucm-meta-data]"
            )
        )
    )

    # Extract all attributes atomically in one JS call before DOM can re-render
    link_data = driver.execute_script(
        """
        var elems = arguments[0];
        return Array.from(elems).map(function(el) {
            return {
                href: el.getAttribute('href'),
                data_href: el.getAttribute('data-href'),
                text: el.textContent.trim()
            };
        });
    """,
        elems,
    )

    valid_links = [d for d in link_data if d.get("href")]

    if not valid_links:
        raise NoValidLinksFound(f"No links found for {source}")

    return link_data


def execute_with_retry(func, retries=3):
    for retry in range(1, retries + 1):
        try:
            return func()
        except (NoValidLinksFound, StaleElementReferenceException) as e:
            if logger:
                logger.warning(f"{e} — retrying ({retry}/{retries})")
            if retry == retries:
                raise
        except Exception:
            # unexpected exception → fail fast
            raise


def anchors_have_href(elements):
    if not elements:
        return False

    for el in elements:
        href = el.get_attribute("href")
        if not href or href.strip() == "":
            return False

    return elements


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
