"""The interface on a phone: what a finger and a small screen run into (#52).

Each test names one thing a phone user hit or could hit; they run against the
real pages, the real CSS and the real map and graph libraries.
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

# the smallest phone in use, a common Android one, and a phone turned sideways
PHONES = ["iPhone SE", "Pixel 7", "iPhone 13 landscape"]
ROUTES = ["#/chat", "#/publication-map", "#/collaboration-graph"]

MAP_READY = "() => document.getElementById('map-status')?.hidden"
FRAME_CENTRE = """(sel) => {
  const r = document.querySelector(sel).getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
}"""


def drag(page, x, y0, y1, steps=12):
    """One finger from (x, y0) to (x, y1), as raw touches the page has to handle itself."""
    cdp = page.context.new_cdp_session(page)
    point = lambda y: [{"x": x, "y": y}]  # noqa: E731
    cdp.send(
        "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": point(y0)}
    )
    for i in range(1, steps + 1):
        y = y0 + (y1 - y0) * i / steps
        cdp.send(
            "Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": point(y)}
        )
        page.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def settle(page, selector, timeout_ms=10_000):
    """Wait until the element draws the same picture twice: a glide has ended."""
    element = page.locator(selector)
    last = element.screenshot()
    for _ in range(timeout_ms // 200):
        page.wait_for_timeout(200)
        now = element.screenshot()
        if now == last:
            return
        last = now
    raise AssertionError(f"{selector} never came to rest")


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("device", PHONES)
def test_no_page_is_wider_than_the_screen(open_page, device, route):
    # a page wider than the layout viewport makes a phone zoom the whole page out
    page = open_page(device, route)
    page.wait_for_timeout(500)
    excess = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert excess <= 0


def test_the_sign_in_page_is_no_wider_than_the_screen(open_page):
    page = open_page("iPhone SE", signed_in=False)
    page.wait_for_selector("input[type=password]")
    excess = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert excess <= 0


# The header once ran out of room between the phone layout and a wide window:
# the name wrapped out of the bar and the last tab scrolled out of sight.
@pytest.mark.parametrize("width", [320, 390, 721, 768, 844, 932, 1024, 1101, 1280])
def test_the_header_shows_every_tab_in_one_row(open_page, width):
    page = open_page("Pixel 7", viewport={"width": width, "height": 700})
    fit = page.evaluate("""() => {
      const top = document.querySelector('.top'), nav = document.querySelector('.nav');
      const rows = [...top.querySelectorAll('.brand, .nav a, .top-actions > *')]
        .filter((e) => e.offsetParent)
        .map((e) => e.getBoundingClientRect())
        .filter((r) => r.top < top.getBoundingClientRect().top || r.bottom > top.getBoundingClientRect().bottom);
      return {hidden: nav.scrollWidth - nav.clientWidth, outside: rows.length};
    }""")
    assert fit == {"hidden": 0, "outside": 0}


FIELDS_UNDER_16PX = """() => [...document.querySelectorAll('input, textarea, select')]
  .filter((e) => e.offsetParent && !['checkbox', 'radio', 'range', 'hidden'].includes(e.type))
  .filter((e) => parseFloat(getComputedStyle(e).fontSize) < 16)
  .map((e) => e.id || e.name || e.type)"""


@pytest.mark.parametrize(
    ("route", "opener"),
    [
        ("#/chat", None),
        ("#/publication-map", None),
        ("#/chat", "#settings-btn"),
        ("#/chat", "#feedback-chip"),
    ],
)
def test_no_field_makes_ios_zoom_in(open_page, route, opener):
    # iOS zooms the whole page into a focused field set below 16px
    page = open_page("iPhone SE", route)
    if opener == "#settings-btn":
        page.locator("#menu > summary").tap()
        page.locator("#settings-btn").tap()
        page.locator("#settings-tabs [data-tab=account]").tap()
    elif opener == "#feedback-chip":
        page.locator(".settings-row >> text=Feedback").tap()
    page.wait_for_timeout(300)
    assert page.evaluate(FIELDS_UNDER_16PX) == []


@pytest.fixture
def map_on_one_paper(open_page):
    """A phone's map searched for one paper, which it frames at the centre with its title."""
    page = open_page("Pixel 7", "#/publication-map")
    page.wait_for_function(MAP_READY)
    cite = page.evaluate(
        "fetch('api/publication-map?clusters=8').then(r => r.json()).then(r => r.points[0].cite)"
    )
    page.locator("#paper-search").fill(cite)
    page.locator("#paper-search").dispatch_event("change")
    page.locator(".map-frame").scroll_into_view_if_needed()
    settle(page, "#deck-canvas")
    x, y = page.evaluate(FRAME_CENTRE, "#deck-canvas")
    return page, cite, x, y


# a finger lands a few pixels off the drawn dot, or on the paper's name
@pytest.mark.parametrize(
    ("dx", "dy"), [(7, 6), (90, 0)], ids=["beside-the-dot", "on-the-title"]
)
def test_a_tap_near_a_paper_opens_its_card(map_on_one_paper, dx, dy):
    page, cite, x, y = map_on_one_paper
    page.touchscreen.tap(x + dx, y + dy)
    # the first pick also builds the picking buffers, which takes a moment
    expect(page.locator("#map-card")).to_contain_text(cite)


def test_one_finger_on_the_map_pans_it_without_scrolling_the_page(open_page):
    page = open_page("Pixel 7", "#/publication-map")
    page.wait_for_function(MAP_READY)
    page.locator(".map-frame").scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    before = page.evaluate("scrollY")
    x, y = page.evaluate(FRAME_CENTRE, "#deck-canvas")
    drag(page, x, y, y - 150)
    page.wait_for_timeout(400)
    assert page.evaluate("scrollY") == before


@pytest.fixture
def graph(open_page):
    page = open_page("Pixel 7", "#/collaboration-graph")
    page.wait_for_selector(".graph-frame .node")
    page.locator(".graph-frame").scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    return page


def test_a_tap_on_a_graph_node_pins_its_card(graph):
    # A tap's emulated hover used to open the card under the finger first, and
    # the click landed on the card: the PI was never pinned.
    name = graph.locator(".graph-frame .node .lab").first.text_content()
    x, y = graph.evaluate(FRAME_CENTRE, ".graph-frame .node .dot")
    graph.touchscreen.tap(x, y)
    card = graph.locator(".graph-card")
    expect(card).to_have_class(re.compile(r"\bon\b"))
    expect(card).to_contain_text(name)
    # only a pinned card has a close button
    expect(card.locator(".close")).to_be_visible()


def test_a_point_just_beside_a_graph_dot_belongs_to_its_pi(graph):
    # A dot is 17-21px across, so a finger landing beside it has to count (#43).
    # Asked of hit-testing, not of a tap: Chromium would snap the tap to the dot
    # by itself, which Safari does not do.
    box = graph.locator(".graph-frame .node .dot").first.bounding_box()
    x, y = box["x"] + box["width"] + 5, box["y"] + box["height"] / 2
    under = f"document.elementFromPoint({x}, {y})?.closest('.node') === document.querySelector('.graph-frame .node')"
    assert graph.evaluate(under)


def test_scrolling_up_while_an_answer_streams_keeps_the_reader_there(open_page):
    page = open_page("Pixel 7")
    page.locator(".starter").first.tap()
    page.wait_for_function(
        "() => document.querySelector('.msg.assistant .md')?.textContent.length > 400"
    )
    page.wait_for_function("() => scrollY > 300")
    x = page.viewport_size["width"] / 2
    drag(page, x, 200, 500)
    page.wait_for_timeout(300)
    left_at = page.evaluate("scrollY")
    # the answer keeps growing below while the reader stays put
    page.wait_for_timeout(1500)
    assert page.evaluate("document.querySelector('.send').classList.contains('stop')")
    assert page.evaluate("scrollY") == left_at


CHIPS_SHOWN = (
    "() => getComputedStyle(document.querySelector('.settings-row')).display !== 'none'"
)


def test_the_chips_under_the_composer_give_way_while_typing(open_page):
    # with the screen keyboard up they left an iPhone SE ~100px of answer
    page = open_page("iPhone SE")
    page.locator(".composer textarea").tap()
    assert not page.evaluate(CHIPS_SHOWN)


def test_the_chips_under_the_composer_return_once_a_question_is_sent(open_page):
    page = open_page("iPhone SE")
    page.locator(".composer textarea").tap()
    page.keyboard.type("How many papers?")
    page.locator(".send").tap()
    # sending closes the keyboard, so the model can be changed for the next question
    page.wait_for_function(CHIPS_SHOWN)


IN_VIEW = """(sel) => {
  const r = document.querySelector(sel).getBoundingClientRect();
  return r.top >= 0 && r.bottom <= innerHeight;
}"""


def test_the_parameters_keep_close_and_apply_on_screen(open_page):
    # taller than a small phone: its head and its Apply once scrolled away
    page = open_page("iPhone SE")
    page.locator(".settings-row summary.chip").first.tap()
    page.locator(".picker-menu >> text=Parameters").tap()
    page.wait_for_timeout(400)
    body = "#dlg-params .dlg-body"
    assert page.evaluate(
        f"(() => {{ const b = document.querySelector('{body}'); return b.scrollHeight > b.clientHeight; }})()"
    )
    page.evaluate(f"document.querySelector('{body}').scrollTop = 200")
    page.wait_for_timeout(200)
    assert page.evaluate(IN_VIEW, "#dlg-params [data-close]")
    assert page.evaluate(IN_VIEW, "#params-apply")
