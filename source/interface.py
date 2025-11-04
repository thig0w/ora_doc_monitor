# -*- coding: utf-8 -*-
# TODO: refactor to generate a package and hanlde logs and
#       the console into the init file.... :(
import os

from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn

load_dotenv()

console = Console()

error_level = os.getenv("LOG_LVL", "ERROR")
logger.remove()
logger.add(
    RichHandler(console=console, show_time=True, show_path=True, rich_tracebacks=True),
    format="{message}",
    level=error_level,
)


progressbar = Progress(
    SpinnerColumn(),
    *Progress.get_default_columns(),
    TimeElapsedColumn(),
    console=console,
    transient=False,
)
