import hashlib
import os
import shutil
from datetime import datetime

from interface import logger, progressbar
from rich.table import Table

now = datetime.now()


def generate_checksums(folder_path: str, output_file: str):
    entries = []
    for fname in sorted(os.listdir(folder_path)):
        if fname == "000_checksumfile.md":
            continue
        fpath = os.path.join(folder_path, fname)
        if os.path.isfile(fpath):
            md5 = hashlib.md5()
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5.update(chunk)
            entries.append(f"{md5.hexdigest()}  {fname}\n")
    with open(output_file, "w") as f:
        f.writelines(entries)
    logger.info(f"Checksums written to {output_file}")


def parse_checksums(checksum_file: str) -> dict[str, str]:
    result = {}
    if not os.path.isfile(checksum_file):
        return result
    with open(checksum_file) as f:
        for line in f:
            line = line.strip()
            if line:
                parts = line.split("  ", 1)
                if len(parts) == 2:
                    result[parts[0]] = parts[1]
    return result


def create_version_folder(folder_name: str = ""):
    # Set the download path
    file_path = os.path.join(os.getcwd(), f"df_{now:%Y%m%d%H%M}/{folder_name}")
    os.makedirs(file_path, exist_ok=True)
    return file_path


def copy_files(diff_tab: list[tuple[str, str, str]], desc: str = ""):
    diff_file_path = create_version_folder(desc)
    for file in diff_tab:
        if file[1] != "":
            root, extension = os.path.splitext(os.path.basename(file[1]))
            shutil.copy(file[1], f"{diff_file_path}/{root}_new{extension}")
        if file[2] != "":
            root, extension = os.path.splitext(os.path.basename(file[2]))
            shutil.copy(file[2], f"{diff_file_path}/{root}_old{extension}")


def draw_result_table(diff_tab: list[tuple[str, str, str]], desc: str = ""):
    table = Table(title=f"{desc} Diff Report")
    table.add_column("Diff Type", justify="center", style="cyan", no_wrap=True)
    table.add_column("Left File", justify="center", style="magenta")
    table.add_column("Right File", justify="center", style="green")

    clean_table = [
        [a, os.path.basename(b), os.path.basename(c)] for a, b, c in diff_tab
    ]

    for diff in clean_table:
        table.add_row(*diff)

    return table


def comp_folders(work_dir: str, base_dir: str, desc: str = ""):
    logger.info(f"Comparing folders: {work_dir} and {base_dir}")

    if not os.path.isdir(work_dir):
        logger.info(f"Work folder not found, skipping: {work_dir}")
        return

    if len(os.listdir(work_dir)) == 0:
        logger.info(f"No files found in {work_dir}")
        return

    # Ensure base exists before comparison
    os.makedirs(base_dir, exist_ok=True)

    # Generate checksums for the freshly downloaded work folder
    work_checksum_file = os.path.join(work_dir, "000_checksumfile.md")
    generate_checksums(work_dir, work_checksum_file)

    # Load and compare hash sets
    work_hashes = parse_checksums(work_checksum_file)
    base_checksum_file = os.path.join(base_dir, "000_checksumfile.md")
    if not os.path.isfile(base_checksum_file):
        generate_checksums(base_dir, base_checksum_file)
    base_hashes = parse_checksums(base_checksum_file)

    logger.info(f"Starting diff report for {desc}...")
    diff_tab: list[tuple[str, str, str]] = []

    # Add a main task for the overall comparison
    diff_task = progressbar.add_task(  # noqa:F841
        f"[cyan]Comparing directories for {desc}...", total=None
    )
    progressbar.start()

    for hash_, fname in work_hashes.items():
        if hash_ not in base_hashes:
            logger.info(f"NEW file: {fname} in {work_dir}")
            diff_tab.append(("[green]LEFT", os.path.join(work_dir, fname), ""))

    for hash_, fname in base_hashes.items():
        if hash_ not in work_hashes:
            logger.info(f"REMOVED file: {fname} in {base_dir}")
            diff_tab.append(("[red]RIGHT", "", os.path.join(base_dir, fname)))

    if len(diff_tab) > 0:
        table = draw_result_table(diff_tab, desc)
        copy_files(diff_tab, desc)
        progressbar.log(table)

    progressbar.stop_task(diff_task)
    progressbar.stop()

    # Remove old files from base (hashes only in base, not in work)
    for hash_, fname in base_hashes.items():
        if hash_ not in work_hashes:
            os.remove(os.path.join(base_dir, fname))
            logger.info(f"Removed old file from base: {fname}")

    # Move new files from work to base (hashes only in work, not in base)
    for hash_, fname in work_hashes.items():
        if hash_ not in base_hashes:
            shutil.move(os.path.join(work_dir, fname), os.path.join(base_dir, fname))
            logger.info(f"Moved new file to base: {fname}")

    # Move 000_checksumfile.md from work to base
    shutil.move(work_checksum_file, os.path.join(base_dir, "000_checksumfile.md"))

    # Clean up disposable work folder
    shutil.rmtree(work_dir)
    logger.info(f"Synced {work_dir} -> {base_dir} and removed work folder")


def diff_auth_folders():
    comp_folders(
        os.path.join(os.getcwd(), "func_docs_work"),
        os.path.join(os.getcwd(), "func_docs"),
        "func_docs",
    )


def diff_noauth_folders(noauth_source: list[dict[str, str]]):
    for i in noauth_source:
        comp_folders(
            os.path.join(os.getcwd(), f"{i['desc']}_work"),
            os.path.join(os.getcwd(), f"{i['desc']}"),
            f"{i['desc']}",
        )


if __name__ == "__main__":
    doc_sources = {
        "noauth_req": [
            {
                "desc": "alloc_docs",
                "doc_id": "https://docs.oracle.com/en/industries/retail/retail-allocation-cloud/latest/books.html",
            },
            {
                "desc": "rfm_docs",
                "doc_id": "https://docs.oracle.com/en/industries/retail/retail-fiscal-management/latest/books.html",
            },
            {
                "desc": "int_docs",
                "doc_id": "https://docs.oracle.com/en/industries/retail/retail-integration-cloud/latest/books.html",
            },
            {
                "desc": "reim_docs",
                "doc_id": "https://docs.oracle.com/en/industries/retail/retail-invoice-matching-cloud/latest/books.html",
            },
            {
                "desc": "rpm_docs",
                "doc_id": "https://docs.oracle.com/en/industries/retail/retail-pricing-cloud/latest/books.html",
            },
            {
                "desc": "mfcs_docs",
                "doc_id": "https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/latest/books.html",
            },
        ]
    }

    diff_auth_folders()
    diff_noauth_folders(doc_sources["noauth_req"])
