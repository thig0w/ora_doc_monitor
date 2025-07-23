# -*- coding: utf-8 -*-
# TODO: refactor to generate a package and hanlde logs and
#       the console into the init file.... :(
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn

console = Console()
progressbar = Progress(
    SpinnerColumn(),
    *Progress.get_default_columns(),
    TimeElapsedColumn(),
    console=console,
    transient=False,
)
