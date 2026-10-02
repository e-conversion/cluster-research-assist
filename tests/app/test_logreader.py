"""Reading the JSON-lines log back for the Logs tab, newest first."""

import json

import pytest

from cra.app.logreader import Query, read


def write_log(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def entry(n, level="INFO", ts=None, msg=None):
    return {
        "ts": ts or f"2026-10-07T10:00:{n:02d}.000+00:00",
        "level": level,
        "logger": "cra.x",
        "msg": msg or f"event {n}",
    }


@pytest.fixture
def logs(tmp_path):
    # the older half has rotated into app.log.1
    write_log(tmp_path / "app.log.1", [entry(n) for n in range(5)])
    write_log(tmp_path / "app.log", [entry(n) for n in range(5, 10)])
    return tmp_path


def messages(page):
    return [e["msg"] for e in page["entries"]]


def test_pages_run_newest_first_across_rotated_files(logs):
    first = read(logs, Query(limit=4))
    second = read(logs, Query(limit=4, **first["next"]))
    third = read(logs, Query(limit=4, **second["next"]))
    shown = messages(first) + messages(second) + messages(third)
    assert shown == [f"event {n}" for n in range(9, -1, -1)]
    assert third["next"] is None


def test_entries_sharing_a_timestamp_are_neither_lost_nor_repeated(tmp_path):
    same = "2026-10-07T10:00:00.000+00:00"
    write_log(tmp_path / "app.log", [entry(n, ts=same) for n in range(5)])
    first = read(tmp_path, Query(limit=2))
    second = read(tmp_path, Query(limit=2, **first["next"]))
    third = read(tmp_path, Query(limit=2, **second["next"]))
    shown = messages(first) + messages(second) + messages(third)
    assert sorted(shown) == [f"event {n}" for n in range(5)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (Query(levels=frozenset({"ERROR"})), ["model failed"]),
        (Query(text="OUT OF"), ["the model endpoint is out of credit"]),
        (Query(after="2026-10-07T10:00:01.000+00:00"), ["model failed"]),
    ],
    ids=["level", "text", "newer than"],
)
def test_a_query_narrows_the_entries(tmp_path, query, expected):
    write_log(
        tmp_path / "app.log",
        [
            entry(0, "WARNING", msg="the model endpoint is out of credit"),
            entry(1, msg="login"),
            entry(2, "ERROR", msg="model failed"),
        ],
    )
    assert messages(read(tmp_path, query)) == expected


def test_a_line_that_is_not_json_is_skipped(tmp_path):
    (tmp_path / "app.log").write_text(
        json.dumps(entry(0)) + "\nTraceback (most recent call last):\n"
    )
    assert messages(read(tmp_path, Query())) == ["event 0"]


def test_a_missing_log_reads_as_empty(tmp_path):
    assert read(tmp_path, Query()) == {"entries": [], "next": None}
