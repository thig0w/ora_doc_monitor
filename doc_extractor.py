# -*- coding: utf-8 -*-
import glob
import os
import sys
from time import sleep, time

from dotenv import load_dotenv
from loguru import logger
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

load_dotenv()

logger.remove(0)
logger.add(sys.stderr, level="ERROR")


# Inicializa o WebDriver
firefox_options = Options()
firefox_options.set_preference("browser.download.folderList", 2)
firefox_options.set_preference("browser.download.manager.showWhenStarting", False)
firefox_options.set_preference(
    "browser.helperApps.neverAsk.saveToDisk",
    "application/octet-stream,application/pdf",
)
firefox_options.set_preference("pdfjs.disabled", True)


def count_files_with_extension(folder_path, extension):
    pattern = os.path.join(folder_path, f"*{extension}")
    files = glob.glob(pattern)
    return len(files)


# Função para esperar que todos os downloads sejam concluídos
def wait_for_downloads(directory, timeout=3600, poll_interval=1):
    """
    Espera que todos os downloads na pasta sejam concluídos.

    :param directory: Caminho para o diretório de downloads.
    :param timeout: Tempo máximo de espera em segundos.
    :param poll_interval: Intervalo entre verificações em segundos.
    """
    last_total = total_partial = count_files_with_extension(directory, ".part")

    with tqdm(total=total_partial, desc="Waiting for Downloads") as pbar:
        end_time = time() + timeout
        while time() < end_time:
            partial = count_files_with_extension(directory, ".part")
            pbar.update(last_total - partial)
            last_total = partial
            if partial == 0:
                return True
            sleep(poll_interval)

        raise TimeoutError(
            "Os downloads não foram concluídos dentro do tempo especificado."
        )


# Wait until the page is fully loaded
def wait_for_page_load(driver, timeout=30):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def open_driver():
    # Get user/pass
    mos_user = os.getenv("MOSUSER")
    mos_pass = os.getenv("MOSPASS")

    if mos_user is None or mos_pass is None:
        logger.error("Please set MOSUSER and MOSPASS environment variables!")
        return False

    try:
        driver = webdriver.Firefox(options=firefox_options)

        # Open the Login Page
        url = "https://support.oracle.com/epmos/faces/KmHome"
        driver.get(url)

        # Setting Driver Wait
        wait = WebDriverWait(driver, 60)

        # Find and fill username
        username_field = wait.until(
            EC.visibility_of_element_located(
                (By.ID, "idcs-signin-basic-signin-form-username")
            )
        )
        username_field.send_keys(mos_user)
        username_field.send_keys(Keys.RETURN)

        # Find and fill the Password
        password_field = wait.until(
            EC.visibility_of_element_located((By.ID, "idcs-auth-pwd-input|input"))
        )
        password_field.send_keys(mos_pass)
        password_field.send_keys(Keys.RETURN)

        # Find and fill the 2fa
        mfa_field = wait.until(
            EC.visibility_of_element_located(
                (By.ID, "idcs-mfa-mfa-auth-sms-email-code-input|input")
            )
        )
        print("Enter the code sent to your phone: ", end="")
        mfa_field.send_keys(input())
        mfa_field.send_keys(Keys.RETURN)

        # Must wait auth
        sleep(10)

        return driver
    except Exception as e:
        logger.error(f"Error trying to Open webdriver and login: {e}")
    else:
        driver.quit()

    return None


def download_docs(driver, url, folder_name):
    try:
        driver.get(url)
        # Set download path
        file_path = os.path.join(os.getcwd(), folder_name)
        os.makedirs(file_path, exist_ok=True)
        firefox_options.set_preference("browser.download.dir", file_path)
        # Wait page load
        logger.debug("Waiting first element to load...")
        # Setting Driver Wait
        wait = WebDriverWait(driver, 60)
        wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "xq1")))
        logger.debug("Waiting for the page to fully load...")
        wait_for_page_load(driver)
        logger.info("Sleep 10 seconds sometime it does not fully load")
        sleep(10)
        elems = driver.find_elements(by=By.XPATH, value="//a[@href]")
        href_links = [e.get_attribute("href") for e in elems]
        logger.debug("Start downloading...")
        for i in tqdm(href_links, desc="Downloading Start Process"):
            if i.__contains__("downloadattachmentprocessor"):
                logger.info(f"Downloading: {i}")
                driver.execute_script(f"window.open('{i}')")
                sleep(0.5)

        wait_for_downloads(file_path)
    except Exception as e:
        logger.error(f"{e}")

    finally:
        # TODO: Check it asks if i want to wait downloads to finish
        sleep(1)
        # Closes the browser
        driver.quit()


if __name__ == "__main__":
    driver = open_driver()
    if driver is not None:
        download_docs(
            driver,
            "https://support.oracle.com/epmos/faces/DocumentDisplay?id=1585843.1",
            "func_docs",
        )
