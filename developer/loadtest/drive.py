"""Simulated workshop: many people behind one address, asking questions.

Each user signs in with their email, opens the page, asks questions with a
pause between them, and one in five also opens the Publication Map and the
Collaboration Graph. Questions of one user in three mention the lab, so the
mock model calls the eLabFTW or DataTagger tools. Meanwhile the server's
memory and CPU (``ps``) and its database pool (the admin console's Server
info) are sampled every second.

    LOADTEST_PASSWORD=… LOADTEST_ADMIN_PASSWORD=… python developer/loadtest/drive.py \\
        --base http://127.0.0.1:8766 --users 50 --pid <cra pid> --out results.json
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import time
from pathlib import Path

import httpx

# a turn that spent its tool budget still answered
ANSWERED = (None, "tool_call_limit_reached", "tool_calls_fruitless")
QUESTIONS = [
    "Who in the cluster works on perovskite interfaces?",
    "Which groups collaborate on battery electrolytes?",
    "What does the cluster publish on photoelectrochemistry?",
]
LAB_QUESTIONS = [
    "Which experiments in our lab notebook involve TGA?",
    "Is there a lab dataset that matches the MOF-5 measurements?",
    "List the lab projects related to catalysis.",
]


async def ask(client: httpx.AsyncClient, prompt: str) -> dict:
    started = time.perf_counter()
    first = None
    # a tool that returns an error is part of a normal answer: the model
    # reads the error and carries on, so it is counted apart from failures
    outcome: dict = {"status": None, "error": None, "tools": [], "tool_errors": 0}
    async with client.stream(
        "POST", "/api/chat", json={"prompt": prompt}, timeout=300
    ) as response:
        outcome["status"] = response.status_code
        if response.status_code != 200:
            outcome["error"] = (await response.aread()).decode()[:200]
            return {**outcome, "total_s": time.perf_counter() - started}
        kind = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:") and kind:
                if kind == "text_delta" and first is None:
                    first = time.perf_counter() - started
                elif kind == "tool_call_end":
                    event = json.loads(line[5:])
                    outcome["tools"].append(event.get("name"))
                    outcome["tool_errors"] += not event.get("ok")
                elif kind in ("done", "error"):
                    event = json.loads(line[5:])
                    if kind == "error" or event.get("error") not in ANSWERED:
                        outcome["error"] = event.get("error_type") or event.get("error")
    return {**outcome, "ttft_s": first, "total_s": time.perf_counter() - started}


async def user(n: int, args, results: list) -> None:
    record: dict = {"user": n, "questions": []}
    results.append(record)
    lab = n % 3 == 0
    await asyncio.sleep(random.uniform(0, args.ramp))
    async with httpx.AsyncClient(base_url=args.base, timeout=60) as client:
        started = time.perf_counter()
        signed = await client.post(
            "/auth/password",
            json={"username": f"attendee{n:02d}@uni.test", "password": args.password},
        )
        record["signin_s"] = time.perf_counter() - started
        record["signin_status"] = signed.status_code
        if signed.status_code != 200:
            return
        pages = ["/", "/api/session"]
        if n % 5 == 0:
            pages += ["/api/publication-map", "/api/collaboration-graph"]
        record["pages"] = []
        for page in pages:
            started = time.perf_counter()
            response = await client.get(page)
            record["pages"].append(
                (page, response.status_code, time.perf_counter() - started)
            )
        for q in range(args.questions):
            await asyncio.sleep(random.uniform(0.5, 1.5) * args.think)
            prompt = (LAB_QUESTIONS if lab else QUESTIONS)[q % 3]
            try:
                record["questions"].append(await ask(client, prompt))
            except httpx.HTTPError as exc:
                record["questions"].append(
                    {"status": None, "error": type(exc).__name__, "total_s": None}
                )


async def sample(args, samples: list, stop: asyncio.Event) -> None:
    async with httpx.AsyncClient(base_url=args.base, timeout=10) as admin:
        await admin.post(
            "/auth/password",
            json={"username": "loadadmin", "password": args.admin_password},
        )
        while not stop.is_set():
            row: dict = {"t": time.time()}
            if args.pid:
                ps = await asyncio.create_subprocess_exec(
                    "ps", "-o", "rss=,%cpu=", "-p", str(args.pid),
                    stdout=asyncio.subprocess.PIPE,
                )  # fmt: skip
                out = (await ps.communicate())[0].decode().split()
                if out:
                    row |= {"rss_mib": int(out[0]) / 1024, "cpu": float(out[1])}
            try:
                info = (await admin.get("/api/admin/server")).json()
                row |= {
                    "db_connections": info["database"].get("connections"),
                    "pool_checked_out": (info["database"]["pool"] or {}).get(
                        "checked_out"
                    ),
                    "turns_running": info["app"]["turns_running"],
                }
            except (httpx.HTTPError, ValueError, KeyError):
                row["admin"] = "unavailable"
            samples.append(row)
            await asyncio.sleep(1)


def pct(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def summary(results: list, samples: list) -> dict:
    questions = [q for r in results for q in r["questions"]]
    signins = [r.get("signin_s") for r in results]
    errors = [q for q in questions if q.get("error") or q.get("status") != 200]
    return {
        "users": len(results),
        "signins_ok": sum(r.get("signin_status") == 200 for r in results),
        "signin_p50_s": pct(signins, 0.5),
        "signin_p95_s": pct(signins, 0.95),
        "page_errors": sum(
            1 for r in results for _, status, _ in r.get("pages", []) if status != 200
        ),
        "page_p95_s": pct([s for r in results for *_, s in r.get("pages", [])], 0.95),
        "questions": len(questions),
        "tool_errors": sum(q.get("tool_errors", 0) for q in questions),
        "question_errors": len(errors),
        "error_kinds": sorted({str(q.get("error") or q.get("status")) for q in errors}),
        "ttft_p50_s": pct([q.get("ttft_s") for q in questions], 0.5),
        "ttft_p95_s": pct([q.get("ttft_s") for q in questions], 0.95),
        "answer_p50_s": pct([q.get("total_s") for q in questions], 0.5),
        "answer_p95_s": pct([q.get("total_s") for q in questions], 0.95),
        "rss_max_mib": max((s.get("rss_mib", 0) for s in samples), default=None),
        "cpu_max_percent": max((s.get("cpu", 0) for s in samples), default=None),
        "cpu_mean_percent": statistics.fmean([s["cpu"] for s in samples if "cpu" in s])
        if any("cpu" in s for s in samples)
        else None,
        "db_connections_max": max(
            (s.get("db_connections") or 0 for s in samples), default=None
        ),
        "pool_checked_out_max": max(
            (s.get("pool_checked_out") or 0 for s in samples), default=None
        ),
        "turns_running_max": max(
            (s.get("turns_running") or 0 for s in samples), default=None
        ),
    }


async def run(args) -> dict:
    results: list = []
    samples: list = []
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample(args, samples, stop))
    await asyncio.gather(*(user(n, args, results) for n in range(args.users)))
    stop.set()
    await sampler
    return {"summary": summary(results, samples), "users": results, "samples": samples}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--questions", type=int, default=3)
    parser.add_argument("--think", type=float, default=20.0, help="seconds, ±50 %%")
    parser.add_argument("--ramp", type=float, default=10.0, help="sign-ins spread over")
    parser.add_argument("--pid", type=int, help="the cra process, for ps")
    parser.add_argument("--out", default="results.json")
    args = parser.parse_args()
    args.password = os.environ["LOADTEST_PASSWORD"]
    args.admin_password = os.environ["LOADTEST_ADMIN_PASSWORD"]
    outcome = asyncio.run(run(args))
    Path(args.out).write_text(json.dumps(outcome))
    print(json.dumps(outcome["summary"], indent=1))


if __name__ == "__main__":
    main()
