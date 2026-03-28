# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`ora_doc_monitor` is a Python CLI tool that monitors and downloads Oracle documentation from two sources:
- **MOS (My Oracle Support):** Requires authenticated Selenium-based browser automation with 2FA
- **Public Oracle docs:** No authentication, uses `requests` + BeautifulSoup to scrape and download PDFs

After downloading, it diffs old vs. new versions and copies changed files to a timestamped output folder.

## Package Manager

This project uses **UV**. Use `uv` for all dependency and environment operations:

```bash
uv sync          # Install dependencies
uv sync --dev    # Install including dev tools (ruff, pre-commit)
uv build         # Build the package
```

## Running the Application

```bash
python -m source.cli                  # Download all docs, then run diff
python -m source.cli --auth_docs      # MOS authenticated docs only
python -m source.cli --no_auth_docs   # Public docs only
python -m source.cli --headed         # Show browser window (default: headless)
python -m source.cli --download       # Skip diff comparison after download
```

## Linting & Formatting

```bash
ruff check source/          # Lint
ruff check source/ --fix    # Lint with auto-fix
ruff format source/         # Format
```

Ruff config is in `pyproject.toml`: target Python 3.12, line length 88, double quotes, rules: E, F, UP, B, SIM, I.

## Required Environment Variables

```
MOSUSER=<Oracle Support username>
MOSPASS=<Oracle Support password>
MOSMFAKEY=<Base32-encoded TOTP key>
LOG_LVL=<DEBUG|INFO|WARNING|ERROR>  # optional, default: ERROR
```

## Architecture

All source code lives in `source/`:

- **`cli.py`** — Click-based entry point. Spawns two threads (auth docs + public docs) in parallel. Each thread runs its own download then immediately triggers its own diff — auth and noauth diffs are fully decoupled and run as soon as their respective downloads finish.
- **`doc_extractor.py`** — Selenium/Firefox automation: logs into MOS with TOTP 2FA, downloads PDFs. Includes retry logic (`execute_with_retry`) and a `watchdog()` to force-kill frozen Firefox processes.
- **`url_extractor.py`** — Downloads PDFs from public Oracle documentation pages by scraping HTML links with BeautifulSoup.
- **`diff_docs.py`** — Compares work vs. base folders by MD5 hash (not filename), copies diffs to `df_YYYYMMDDHHMM/`, and renders a Rich table (LEFT = new, RIGHT = removed). Renamed-but-unchanged files are ignored. Syncs base folder by removing/moving only changed files, then stores `000_checksumfile.md` in the base folder for the next run. Exposes `diff_auth_folders()` and `diff_noauth_folders()` as separate entry points.
- **`interface.py`** — Shared `logger` (loguru + Rich handler), `progressbar` (Rich Progress), and `console` (Rich Console) used across all modules.
- **`doc_sources.json`** — Configuration listing all documentation sources: `auth_req` (MOS doc IDs) and `noauth_req` (public URLs with folder names).

### Download Output Structure

- `func_docs/` — MOS authenticated docs baseline (persistent); includes `000_checksumfile.md`
- `<name>_docs/` — Public doc baseline per source (persistent); includes `000_checksumfile.md`
- `func_docs_work/` / `<name>_work/` — Temporary download targets; deleted after sync
- `df_YYYYMMDDHHMM/` — Diff output: `*_new.*` and `*_old.*` pairs (only created when differences are detected)

### Key Design Patterns

- **Oracle JET detection:** `doc_extractor.py` waits for Oracle's JavaScript framework to finish rendering before interacting with pages.
- **Firefox PDF auto-save:** WebDriver preferences configure Firefox to auto-download PDFs without dialogs.
- **No test suite** currently exists in this project.
