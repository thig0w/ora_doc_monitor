# -*- coding: utf-8 -*-
import os
import sys

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

error_level = os.getenv("LOG_LVL", "ERROR")
logger.remove(0)
logger.add(sys.stderr, level=error_level)
