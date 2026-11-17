"""A phone-sized Chromium against the app served by serve.py.

The pages load deck.gl, d3 and marked from their CDN, so these tests need the
network as well as ``playwright install chromium``. Chromium stands in for
both phones: it emulates the viewport, touch and the media queries
(``hover: none``, ``pointer: coarse``) the interface switches on, which is
what these tests are about; Safari's own quirks are not reproduced.
"""

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

HERE = Path(__file__).resolve().parent
PASSWORD = "correct horse battery"
STARTUP_S = 30


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    port = _free_port()
    workdir = tmp_path_factory.mktemp("browser-server")
    log = (workdir / "server.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "serve.py"), str(port), str(workdir)],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        deadline = time.monotonic() + STARTUP_S
        while True:
            try:
                urllib.request.urlopen(url + "api/config", timeout=1)
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    log.flush()
                    pytest.fail(
                        f"serve.py did not start:\n{(workdir / 'server.log').read_text()}"
                    )
                time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


@pytest.fixture(scope="session")
def playwright():
    with sync_api.sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def chromium(playwright):
    browser = playwright.chromium.launch()
    yield browser
    browser.close()


@pytest.fixture
def open_page(playwright, chromium, base_url):
    """open_page(device, route, signed_in=True) -> a page showing that route."""
    contexts = []

    def open_(device, route="#/chat", signed_in=True, **overrides):
        options = {**playwright.devices[device], **overrides}
        options.pop("default_browser_type", None)
        # the page's CSP forbids the string evaluation wait_for_function uses
        context = chromium.new_context(base_url=base_url, bypass_csp=True, **options)
        contexts.append(context)
        if signed_in:
            response = context.request.post(
                "auth/password", data={"username": "alice", "password": PASSWORD}
            )
            assert response.ok, response.text()
        page = context.new_page()
        page.goto(route)
        page.wait_for_load_state("networkidle")
        return page

    yield open_
    for context in contexts:
        context.close()
