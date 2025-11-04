# -*- coding: utf-8 -*-

import json
import os
import sys
import threading

import click
from diff_docs import diff_all_folders
from doc_extractor import download_docs, open_driver
from interface import logger
from url_extractor import download_pdfs


def read_json():
    logger.info("Reading json file")
    file_path = os.path.join(os.path.dirname(__file__), "doc_sources.json")
    with open(file_path) as f:
        return json.load(f)


@click.command()
@click.option(
    "-a", "--auth_docs", is_flag=True, help="Download docs with authentication required"
)
@click.option("-n", "--no_auth_docs", is_flag=True, help="Download docs without auth")
@click.option("-h", "--headed", is_flag=True, help="Run browser in headless mode")
@click.option("-d", "--download", is_flag=True, help="Download only, do not run diff")
def get_docs(auth_docs, no_auth_docs, headed, download):
    is_both = not (auth_docs or no_auth_docs)
    logger.info("Starting from CLI")

    # read the source file
    doc_sources = read_json()

    # infos that need auth
    if auth_docs or is_both:
        driver = open_driver(headed=headed)
        if driver is None:
            sys.exit(1)
        thread_docs_auth = threading.Thread(
            target=download_docs, args=(doc_sources["auth_req"], driver)
        )
        thread_docs_auth.start()

    if no_auth_docs or is_both:
        thread_docs_noauth = threading.Thread(
            target=download_pdfs, args=(doc_sources["noauth_req"],)
        )
        thread_docs_noauth.start()

    # Wait for threads to complete
    if auth_docs or is_both:
        thread_docs_auth.join()
    if no_auth_docs or is_both:
        thread_docs_noauth.join()

    if not download:
        diff_all_folders(doc_sources["noauth_req"])


if __name__ == "__main__":
    get_docs()
