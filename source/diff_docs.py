# -*- coding: utf-8 -*-
import filecmp
import os

from interface import logger, progressbar
from rich.table import Table


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

    # console.print(table)
    return table


def comp_folders(dir1, dir2, desc: str = ""):
    logger.info(f"Comparing folders: {dir1} and {dir2}")
    logger.info(f"Starting diff report for {desc}...")
    diff_tab: list[tuple[str, str, str]] = []

    # Add a main task for the overall comparison
    main_diff_task = progressbar.add_task(  # noqa:F841
        f"[cyan]Comparing directories for {desc}...", total=None
    )
    progressbar.start()

    dcmp = filecmp.dircmp(dir1, dir2)

    for name in dcmp.diff_files:
        logger.info(f"DIFF file: {name} found in {dcmp.left} and {dcmp.right}")
        diff_tab.append(("[cyan]DIFF", f"{dcmp.left}/{name}", f"{dcmp.right}/{name}"))
    for name in dcmp.left_only:
        logger.info(f"ONLY LEFT file: {name} found in {dcmp.left}")
        diff_tab.append(("[green]LEFT", f"{dcmp.left}/{name}", ""))
    for name in dcmp.right_only:
        logger.info(f"ONLY RIGHT file: {name} found in {dcmp.right}")
        diff_tab.append(("[red]RIGHT", "", f"{dcmp.right}/{name}"))
    ## This process does not intend to generate subfolders. So no need to compare
    # for sub_dcmp in dcmp.subdirs.values():
    #     sub_diff_tab = report_recursive_diff(sub_dcmp)
    #     diff_tab.extend(sub_diff_tab)

    if len(diff_tab) > 0:
        table = draw_result_table(diff_tab, desc)
        progressbar.log(table)

    progressbar.stop()


if __name__ == "__main__":
    comp_folders(
        os.path.join(os.getcwd(), "../func_docs"),
        os.path.join(os.getcwd(), "../func_docs_old"),
        "func_docs",
    )
    # draw_result_table([("left","file1","file2"),
    #                    ("right","","file4")])
