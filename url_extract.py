# -*- coding: utf-8 -*-
import os
import sys
from time import sleep
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tqdm import tqdm

error_level = os.getenv("LOG_LVL", "ERROR")
logger.remove(0)
logger.add(sys.stderr, level=error_level)

# Simulates a browser
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"  # noqa: E501
}


def download_pdfs(url, folder_name="mom_func_docs"):
    # makes a foler to download files
    output_dir = os.path.join(os.getcwd(), folder_name)
    os.makedirs(output_dir, exist_ok=True)
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Finds all .pdf files
        pdf_links = soup.find_all("a", href=lambda href: href and href.endswith(".pdf"))

        logger.info(f"{len(pdf_links)} PDFs found! Starting Downloading")
        for link in tqdm(pdf_links, desc=f"{folder_name} links"):
            pdf_url = link["href"]

            # appends the url for relative links
            if not pdf_url.startswith("http"):
                pdf_url = urljoin(url, pdf_url)

            # generate the file name from the url name
            filename = os.path.join(output_dir, pdf_url.split("/")[-1])

            # Dowload the file
            logger.info(f"Downloading: {pdf_url}")
            try:
                pdf_response = requests.get(pdf_url, headers=headers)
                pdf_response.raise_for_status()
            except Exception as e:
                logger.error(f"Error trying to download, re-queuing: {e}")
                if pdf_links.count(link) < 5:
                    pdf_links.append(link)
                else:
                    logger.critical(f"Failed to download: {link}")
                continue

            with open(filename, "wb") as pdf_file:
                pdf_file.write(pdf_response.content)

            logger.debug(f"File Saved: {filename}")
            # sleep to avoid connection to be blocked
            sleep(1)
    except Exception as e:
        logger.error(f"Failed: {e}")


if __name__ == "__main__":
    sources = {
        "alloc_docs": "https://docs.oracle.com/en/industries/retail/retail-allocation-cloud/latest/books.html",
        "rfm_docs": "https://docs.oracle.com/en/industries/retail/retail-fiscal-management/latest/books.html",
        "int_docs": "https://docs.oracle.com/en/industries/retail/retail-integration-cloud/latest/books.html",
        "reim_docs": "https://docs.oracle.com/en/industries/retail/retail-invoice-matching-cloud/latest/books.html",
        "rpm_docs": "https://docs.oracle.com/en/industries/retail/retail-pricing-cloud/latest/books.html",
        "mfcs_docs": "https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/latest/books.html",
    }

    for source in tqdm(sources, desc="Sources"):
        # for source in sources:
        download_pdfs(sources[source], source)
