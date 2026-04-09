import json
import os
import sys
import threading

import click
from diff_docs import diff_auth_folders, diff_noauth_folders
from doc_extractor import download_docs, open_driver
from interface import logger, progressbar
from url_extractor import download_pdfs


def _auth_download_and_diff(sources, driver, auth_result, run_diff):
    download_docs(sources, driver, auth_result)
    if run_diff and auth_result[0]:
        diff_auth_folders()


def _noauth_download_and_diff(sources, run_diff):
    download_pdfs(sources)
    if run_diff:
        diff_noauth_folders(sources)


def read_json():
    logger.debug("Reading json file")
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
    auth_result = [True]
    if auth_docs or is_both:
        driver = open_driver(headed=headed)
        if driver is None:
            sys.exit(1)
        thread_docs_auth = threading.Thread(
            target=_auth_download_and_diff,
            args=(doc_sources["auth_req"], driver, auth_result, not download),
        )

    if no_auth_docs or is_both:
        thread_docs_noauth = threading.Thread(
            target=_noauth_download_and_diff,
            args=(doc_sources["noauth_req"], not download),
        )

    with progressbar:
        if auth_docs or is_both:
            thread_docs_auth.start()
        if no_auth_docs or is_both:
            thread_docs_noauth.start()

        if auth_docs or is_both:
            thread_docs_auth.join()
        if no_auth_docs or is_both:
            thread_docs_noauth.join()


if __name__ == "__main__":
    get_docs()
