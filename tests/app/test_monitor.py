"""The machine and process figures behind the Server info tab."""

import pytest

from cra.app import monitor


def test_host_memory_is_read_in_bytes(tmp_path):
    (tmp_path / "meminfo").write_text(
        "MemTotal:        4000 kB\nMemFree:  100 kB\nMemAvailable:    1300 kB\n"
        "SwapTotal:       0 kB\nSwapFree:        0 kB\n"
    )
    assert monitor.host_memory(tmp_path) == {
        "total": 4000 * 1024,
        "available": 1300 * 1024,
        "swap_total": 0,
        "swap_free": 0,
    }


def test_without_proc_there_is_no_host_memory(tmp_path):
    assert monitor.host_memory(tmp_path) is None


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"memory.current": "500\n", "memory.max": "2147483648\n"}, 2147483648),
        ({"memory.current": "500\n", "memory.max": "max\n"}, None),
        (
            {
                "memory/memory.usage_in_bytes": "500",
                "memory/memory.limit_in_bytes": "9223372036854771712",
            },
            None,
        ),
    ],
    ids=["v2 limited", "v2 unlimited", "v1 unlimited"],
)
def test_the_container_limit_is_read_from_the_cgroup(tmp_path, files, expected):
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(content)
    assert monitor.container_memory(tmp_path) == {"used": 500, "limit": expected}


def test_cpu_use_needs_two_samples():
    now = iter([0.0, 10.0])
    probe = monitor.Monitor(clock=lambda: next(now))
    assert probe.cpu_percent() is None
    assert probe.cpu_percent() is not None


def test_the_credit_is_remembered_for_a_while():
    now = [0.0]
    probe = monitor.Monitor(clock=lambda: now[0])
    probe.remember_credit({"limit_remaining": 19.0})
    assert probe.cached_credit() == (True, {"limit_remaining": 19.0})
    now[0] = monitor.CREDIT_TTL_S + 1
    assert probe.cached_credit() == (False, None)
