# -*- coding: utf-8 -*-


import click
from doc_extractor import download_docs
from logger import logger
from url_extract import download_pdfs


@click.command()
# @click.option("-s", "--start_month", type=int, default=1)
# @click.argument("year", type=int, default=date.today().year)
def get_docs():  # (year, start_month):
    logger.info("Starting from CLI")
    # TODO: thread this so both can run in parallel
    # infos that need auth
    download_docs(
        [
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
    )

    # no auth sources
    sources = {
        "alloc_docs": "https://docs.oracle.com/en/industries/retail/retail-allocation-cloud/latest/books.html",
        "rfm_docs": "https://docs.oracle.com/en/industries/retail/retail-fiscal-management/latest/books.html",
        "int_docs": "https://docs.oracle.com/en/industries/retail/retail-integration-cloud/latest/books.html",
        "reim_docs": "https://docs.oracle.com/en/industries/retail/retail-invoice-matching-cloud/latest/books.html",
        "rpm_docs": "https://docs.oracle.com/en/industries/retail/retail-pricing-cloud/latest/books.html",
        "mfcs_docs": "https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/latest/books.html",
    }

    # for source in sources:
    download_pdfs(sources)


if __name__ == "__main__":
    get_docs()
