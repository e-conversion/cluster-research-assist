import json
import logging

from cra.logsetup import configure_logging


def _fail():
    raise ValueError("boom")


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_records_are_json_lines_with_fields(tmp_path):
    root = configure_logging(tmp_path)
    logging.getLogger("cra.test").info("hello", extra={"fields": {"turn": 1}})
    root.handlers[-1].flush()
    (line,) = _lines(tmp_path / "app.log")
    assert line["msg"] == "hello"
    assert line["turn"] == 1
    assert line["logger"] == "cra.test"
    assert line["level"] == "INFO"


def test_exceptions_are_rendered_into_the_line(tmp_path):
    root = configure_logging(tmp_path)
    try:
        _fail()
    except ValueError:
        logging.getLogger("cra.test").exception("failed")
    root.handlers[-1].flush()
    (line,) = _lines(tmp_path / "app.log")
    assert "ValueError: boom" in line["exc"]


def test_reconfiguring_replaces_handlers(tmp_path):
    configure_logging(tmp_path / "a")
    root = configure_logging(tmp_path / "b")
    assert len(root.handlers) == 2
    assert not root.propagate
