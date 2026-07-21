"""
selenium_scraper.py

Fallback for counties whose property search is a JS-rendered SPA (no
plain HTML form to POST to, results populated via client-side JS calling
a JSON API that's impractical to reverse-engineer). Only imported/used
when a county's config has search_type == "selenium", so requests-only
users never need Chrome/Selenium installed at all.

Uses the same county-config "row_selector" / "fields" contract as
scraper.parse_results(), so results from Selenium and from requests+BS4
are interchangeable to the rest of the pipeline - main.py doesn't care
which one produced the HTML it eventually parses.
"""

from __future__ import annotations

import random
import time

from logger import get_logger

PAGE_LOAD_TIMEOUT = 25
RESULTS_WAIT_TIMEOUT = 15


class SeleniumUnavailableError(Exception):
    """Raised when selenium/webdriver isn't installed or a driver can't
    be started, so main.py can report a clean ERROR instead of a stack
    trace for users who never installed the optional JS-fallback deps.
    """


def _build_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise SeleniumUnavailableError(
            "selenium is not installed. Run `pip install selenium "
            "webdriver-manager` to enable JS-rendered county support."
        ) from exc

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--no-sandbox")

    try:
        # webdriver-manager auto-downloads a matching chromedriver so users
        # don't have to hand-install one.
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SeleniumUnavailableError(
            f"Could not start a Chrome webdriver session: {exc}"
        ) from exc


def search_selenium(config: dict, owner_name: str) -> tuple[str, str]:
    """Load the county's search page, fill in the owner-name box, submit,
    wait for results to render, and return (page_source_html, url) for
    scraper.parse_results() to consume.
    """
    logger = get_logger()
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    driver = _build_driver()
    try:
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        url = config["search_url"]
        logger.debug("Selenium: loading %s", url)
        driver.get(url)

        # Small human-like pause before interacting, on top of the
        # rate-limiter delay main.py already applies between rows.
        time.sleep(random.uniform(0.5, 1.5))

        search_box = WebDriverWait(driver, RESULTS_WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, config["selenium_search_box_selector"])
            )
        )
        search_box.clear()
        search_box.send_keys(owner_name)

        submit = driver.find_element(By.CSS_SELECTOR, config["selenium_submit_selector"])
        submit.click()

        WebDriverWait(driver, RESULTS_WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, config["selenium_wait_selector"])
            )
        )
        # Let any post-render JS (e.g. lazy row population) settle.
        time.sleep(random.uniform(0.5, 1.0))

        return driver.page_source, driver.current_url
    finally:
        driver.quit()
