# -*- coding: utf-8 -*-

import sys
import threading

import click
from doc_extractor import download_docs, open_driver
from logger import logger
from url_extract import download_pdfs


@click.command()
@click.option(
    "-a", "--auth_docs", is_flag=True, help="Download docs with authentication required"
)
@click.option("-n", "--no_auth_docs", is_flag=True, help="Download docs without auth")
@click.option("-h", "--headed", is_flag=True, help="Run browser in headless mode")
def get_docs(auth_docs, no_auth_docs, headed):  # (year, start_month):
    is_both = not (auth_docs or no_auth_docs)
    logger.info("Starting from CLI")

    # TODO: transform the sources into a config file
    auth_sources = [
        # Merch functional docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=1585843.1",
        # Extensions docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2978473.1",
        # Rics func docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2643542.1",
        # RDS func docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2899701.1",
        # POM func docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2815461.1",
        # Localization func docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2534504.2",
        # blueprint func docs
        "https://support.oracle.com/epmos/faces/DocumentDisplay?id=2677553.1",
    ]

    # no auth sources
    sources = {
        "alloc_docs": "https://docs.oracle.com/en/industries/retail/retail-allocation-cloud/latest/books.html",
        "rfm_docs": "https://docs.oracle.com/en/industries/retail/retail-fiscal-management/latest/books.html",
        "int_docs": "https://docs.oracle.com/en/industries/retail/retail-integration-cloud/latest/books.html",
        "reim_docs": "https://docs.oracle.com/en/industries/retail/retail-invoice-matching-cloud/latest/books.html",
        "rpm_docs": "https://docs.oracle.com/en/industries/retail/retail-pricing-cloud/latest/books.html",
        "mfcs_docs": "https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/latest/books.html",
    }

    # infos that need auth
    if auth_docs or is_both:
        driver = open_driver(headed=headed)
        if driver is None:
            sys.exit(1)
        thread_docs_auth = threading.Thread(
            target=download_docs, args=(auth_sources, driver)
        )
        thread_docs_auth.start()

    if no_auth_docs or is_both:
        thread_docs_noauth = threading.Thread(target=download_pdfs, args=(sources,))
        thread_docs_noauth.start()

    # Wait for threads to complete
    if auth_docs or is_both:
        thread_docs_auth.join()
    if no_auth_docs or is_both:
        thread_docs_noauth.join()


if __name__ == "__main__":
    get_docs()
