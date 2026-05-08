import json
import os
import threading

import click
from auth_extractor import download_docs as download_auth_docs
from diff_docs import diff_auth_folders, diff_noauth_folders
from interface import logger, progressbar
from url_extractor import download_pdfs


def _auth_download_and_diff(
    sources, headed, auth_result, run_diff, login_done, workers
):
    # Playwright must be instantiated inside the thread that uses it — its sync
    # handles cannot cross threads. download_auth_docs owns the full lifecycle
    # and spawns ``workers`` sub-threads that share a single logged-in session.
    download_auth_docs(
        sources,
        headed=headed,
        result=auth_result,
        login_done=login_done,
        workers=workers,
    )
    if run_diff and auth_result[0]:
        diff_auth_folders()


def _noauth_download_and_diff(sources, run_diff, login_done):
    # Hold off network-heavy public downloads until the MOS login completes —
    # running them in parallel with SSO was slowing the browser-side handshake.
    if login_done is not None:
        login_done.wait()
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
@click.option(
    "-w",
    "--workers",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Parallel worker browsers for auth docs (share a single login).",
)
def get_docs(auth_docs, no_auth_docs, headed, download, workers):
    is_both = not (auth_docs or no_auth_docs)
    logger.info("Starting from CLI")

    # read the source file
    doc_sources = read_json()

    # infos that need auth
    auth_result = [True]
    # Gate for the noauth thread: only starts downloading after MOS login
    # completes. If auth is not being run, the gate is pre-set so the noauth
    # thread proceeds immediately.
    login_done = threading.Event()
    if not (auth_docs or is_both):
        login_done.set()

    if auth_docs or is_both:
        thread_docs_auth = threading.Thread(
            target=_auth_download_and_diff,
            args=(
                doc_sources["auth_req"],
                headed,
                auth_result,
                not download,
                login_done,
                workers,
            ),
        )

    if no_auth_docs or is_both:
        thread_docs_noauth = threading.Thread(
            target=_noauth_download_and_diff,
            args=(doc_sources["noauth_req"], not download, login_done),
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
