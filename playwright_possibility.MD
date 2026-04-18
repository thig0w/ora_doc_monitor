# Playwright Migration Possibility

## Opinion / Executive Summary

**Verdict: Valid migration — with one caveat to validate.**

The migration is worthwhile primarily because the three most painful aspects of the current
Selenium implementation in `source/doc_extractor.py` map directly to Playwright's core strengths:

- **Stale element exceptions** are the #1 reliability hazard. There are currently 4 defensive
  layers: `ignored_exceptions` on `WebDriverWait`, atomic JS extraction, `execute_with_retry`,
  and fresh re-query before every JavaScript-href click. Playwright eliminates this class of
  problem entirely via lazy `Locator` objects that re-query the DOM on every action.

- **Explicit waits and Oracle JET readiness polling** are verbose. Three identical JET readiness
  blocks exist across the file, plus a custom `anchors_have_href` validator and a 3-hour `.part`
  file polling loop. Playwright's auto-wait makes most of this implicit.

- **Firefox profile accumulation and geckodriver management** add fragile teardown code
  (`shutil.rmtree`, `moz:profile` extraction via `driver.capabilities`). Playwright manages the
  browser lifecycle internally and cleans up automatically.

**The one genuine risk** is Oracle JET session-tied downloads via `javascript:` hrefs. The current
code re-finds link elements by index immediately before each `.click()` because the download token
is only issued through the JET click handler. Playwright's `locator.nth(idx).click()` achieves the
same pattern, but the behavior of Oracle's JET click handler through Playwright's CDP bridge must
be validated against a live MOS session before committing to the migration.

---

## Selenium vs. Playwright Comparison

| Concern | Selenium (current) | Playwright |
|---|---|---|
| Stale element exceptions | 4 defensive layers (ignored_exceptions, atomic JS, execute_with_retry, fresh re-query per click) | Eliminated — Locators are lazy and re-evaluated on every action |
| Explicit waits | `WebDriverWait(driver, 60)` + 3 repeated JET readiness blocks | Auto-wait built in; `page.wait_for_function()` only needed for custom JET checks |
| Oracle JET readiness | Manual `execute_script("return window.oj && oj.Context...")` called 3 times | `page.wait_for_function()` with same JS string |
| Download handling | Firefox preferences: MIME types, `pdfjs.disabled`, folder path, no-dialog flags | `browser.new_context(accept_downloads=True)` + `page.expect_download()` context manager |
| Headless mode | `Options().add_argument("--headless")` | `launch(headless=True)` — first-class API param |
| Browser binary management | Requires geckodriver on PATH or via webdriver-manager | `playwright install firefox` — self-contained, versioned binaries |
| Profile cleanup | Manual: `driver.capabilities["moz:profile"]` + `shutil.rmtree` | Automatic — `browser_context.close()` handles it |
| Background throttling workaround | 6 Firefox prefs for headed mode (dom.min_timeout_value, occlusion tracking, etc.) | Not needed — Playwright does not throttle background contexts |
| JavaScript execution | `driver.execute_script()` | `page.evaluate()` — identical capability, same JS strings work |
| Multi-tab / window.open | `execute_script("window.open(url)")` | `context.expect_page()` — event-driven, cleaner resource tracking |
| Session-tied JS clicks | `find_elements()[idx].click()` re-queried fresh per iteration | `page.locator("a[data-oce-meta-data], a[data-ucm-meta-data]").nth(idx).click()` |
| Download progress polling | Custom `.part` file watcher, 15 s poll interval, 3 h timeout | `download.path()` blocks per file until complete; no polling loop needed |
| Watchdog / force-kill | `threading.Timer` + `psutil.Process.children().kill()` | Largely unnecessary — `browser.close()` is reliable; simplify or remove |
| Test-friendliness | No test suite; Selenium harder to record/replay | Built-in trace, HAR, screenshot, video recording; `pytest-playwright` is mature |
| Sync API | Native | `playwright.sync_api` — available; no asyncio rewrite required |
| Active maintenance | Selenium 4.x, slower release cadence | Updated weekly by Microsoft; broadly used in CI |
| Firefox support | Yes (via geckodriver) | Yes (bundled Playwright Firefox build) |

---

## Refactoring Plan

### Step 1 — Dependencies (`pyproject.toml`)

- Remove: `selenium>=4.27.1`
- Add: `playwright>=1.44`
- Keep: `pyotp`, `psutil` (still useful if a watchdog is retained), `loguru`, `rich`
- Add dev (optional): `pytest-playwright` to unlock a future test suite

```bash
uv add playwright
uv remove selenium
playwright install firefox
```

---

### Step 2 — `open_driver()` → `open_browser()`

Current signature returns a bare `driver: webdriver.Firefox`.

New signature returns a tuple `(pw, browser, context, page)`:

```python
from playwright.sync_api import sync_playwright

def open_browser(headed: bool = False, file_path: str = ...):
    pw = sync_playwright().start()
    browser = pw.firefox.launch(headless=not headed)
    context = browser.new_context(
        accept_downloads=True,
    )
    page = context.new_page()
    return pw, browser, context, page
```

This replaces all `set_preference()` calls, the `--headless` argument, the headed-mode throttle
workarounds, and the `shutil.rmtree(moz:profile)` teardown.

---

### Step 3 — Login / 2FA Flow

| Selenium | Playwright |
|---|---|
| `wait.until(ec.visibility_of_element_located((By.ID, x)))` | `page.locator("#x").wait_for(state="visible")` |
| `element.send_keys(value)` | `page.locator("#x").fill(value)` |
| `element.send_keys(Keys.RETURN)` | `page.keyboard.press("Enter")` |
| `Select(el).select_by_value(v)` | `page.locator("select#id").select_option(v)` |

TOTP generation (`TOTP(mos_mfa_key).now()`) is framework-agnostic — no change needed.

---

### Step 4 — Oracle JET Readiness Checks

Current (repeated 3 times):

```python
wait.until(lambda d: d.execute_script("""
    return window.oj &&
           oj.Context.getPageContext().getBusyContext().isReady();
"""))
wait.until(lambda d: "oj-complete" in
    d.find_element(By.CSS_SELECTOR, "oj-vb-content").get_attribute("class"))
```

Playwright equivalent:

```python
page.wait_for_function(
    "window.oj && oj.Context.getPageContext().getBusyContext().isReady()"
)
page.locator("oj-vb-content.oj-complete").wait_for()
```

The same JavaScript strings work unchanged in `page.wait_for_function()`.

---

### Step 5 — Link Collection (`load_page_and_collect_links`)

Current flow: `wait.until(lambda d: anchors_have_href(...))` + atomic JS extraction.

Playwright equivalent:

```python
# Wait for at least one link with a real href attribute
page.wait_for_selector(
    "a[data-oce-meta-data][href], a[data-ucm-meta-data][href]"
)

# Atomic attribute extraction (same JS logic)
elements = page.query_selector_all(
    "a[data-oce-meta-data], a[data-ucm-meta-data]"
)
link_data = page.evaluate(
    """(elems) => elems.map(el => ({
        href: el.getAttribute('href'),
        data_href: el.getAttribute('data-href'),
        text: el.textContent.trim()
    }))""",
    elements,
)
```

The `anchors_have_href` validator and its associated wait lambda can be removed.

---

### Step 6 — Download Loop

**JavaScript-href links** (session-tied, must go through JET click handler):

```python
# Lazy locator re-queries DOM on each .nth().click() — no stale element risk
locator = page.locator("a[data-oce-meta-data], a[data-ucm-meta-data]")
locator.nth(idx).click()
```

**Regular HTTPS hrefs** (direct download URLs):

```python
with page.expect_download() as dl_info:
    page.evaluate(f"window.open('{href}')")
download = dl_info.value
download.save_as(os.path.join(file_path, download.suggested_filename))
```

**Download completion**: `download.path()` blocks until the file is fully written —
the entire `wait_for_downloads()` polling loop and `.part` file watcher can be removed.

---

### Step 7 — Watchdog

Playwright's `browser.close()` is reliable enough that the 4-second `threading.Timer` watchdog
and `psutil` child-process kill are no longer needed for the common case. A simplified fallback
can be kept as a last resort:

```python
try:
    # normal teardown
    context.close()
    browser.close()
    pw.stop()
except Exception:
    # Playwright cleanup failed — hard exit
    os._exit(1)
```

---

### Step 8 — `execute_with_retry`

Remove `StaleElementReferenceException` from the retry set (it can no longer be raised).
Retain the retry wrapper for `NoValidLinksFound` only:

```python
def execute_with_retry(func, retries=3):
    for retry in range(1, retries + 1):
        try:
            return func()
        except NoValidLinksFound as e:
            logger.warning(f"{e} — retrying ({retry}/{retries})")
            if retry == retries:
                raise
        except Exception:
            raise
```

---

## Verification Plan

After the migration is complete:

1. `uv sync` — confirm no dependency conflicts
2. `playwright install firefox` — confirm browser binary installs cleanly
3. `python -m source.cli --auth_docs --headed` — watch login + 2FA flow visually
4. Confirm PDFs land in `func_docs_work/` without any `.part` file residue
5. `python -m source.cli --auth_docs` — headless end-to-end run
6. `python -m source.cli --no_auth_docs` — confirm `url_extractor.py` path has no regression
7. `python -m source.cli` — full run; verify `df_YYYYMMDDHHMM/` diff output is produced when docs change
