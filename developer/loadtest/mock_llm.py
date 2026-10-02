"""An OpenAI-compatible endpoint that answers like a tool-using model, for free.

Each turn calls one or two tools and then answers, paced like a hosted model:
a pause before the first token, then a steady stream. A question that
mentions the lab asks the eLabFTW or DataTagger tools when the turn offers
them. Usage is reported in the last chunk, roughly four characters a token.

    python developer/loadtest/mock_llm.py --port 8790
"""

import argparse
import asyncio
import json
import random
import time

from hypercorn.asyncio import serve
from hypercorn.config import Config
from quart import Quart, Response, request

app = Quart(__name__)
FIRST_TOKEN_S = (0.8, 2.0)
TOOL_ROUND_S = (0.8, 1.6)
ANSWER_CHUNKS = 60
CHUNK_S = 0.03
ANSWER = (
    "Several groups in the cluster work on this. The papers found point to "
    "the groups of the PIs listed above, with the most recent work in 2025. "
)


def _chunk(delta, finish=None, usage=None):
    body = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


def _next_call(messages, tools):
    names = {t["function"]["name"] for t in tools or []}
    rounds = sum(1 for m in messages if m.get("role") == "tool")
    question = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    lab = "lab" in question.lower()
    plan = [("search_papers", {"query": question[:60], "limit": 10})]
    if lab and "elab_list_experiments" in names:
        plan.append(("elab_list_experiments", {"query": "TGA"}))
    elif lab and "dt_list_projects" in names:
        plan.append(("dt_list_projects", {}))
    else:
        plan.append(("find_experts", {"topic": question[:40]}))
    return plan[rounds] if rounds < len(plan) else None


@app.post("/v1/chat/completions")
async def completions():
    body = await request.get_json()
    messages = body.get("messages", [])
    call = (
        None
        if body.get("tool_choice") == "none"
        else _next_call(messages, body.get("tools"))
    )
    prompt_tokens = len(json.dumps(messages)) // 4

    async def stream():
        if call is not None:
            await asyncio.sleep(random.uniform(*TOOL_ROUND_S))
            name, arguments = call
            yield _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{random.getrandbits(32):x}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                }
            )
            yield _chunk(
                {},
                "tool_calls",
                {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 30,
                    "total_tokens": prompt_tokens + 30,
                },
            )
        else:
            await asyncio.sleep(random.uniform(*FIRST_TOKEN_S))
            words = (ANSWER * 4).split(" ")
            per = max(1, len(words) // ANSWER_CHUNKS)
            for i in range(0, len(words), per):
                yield _chunk({"content": " ".join(words[i : i + per]) + " "})
                await asyncio.sleep(CHUNK_S)
            completion = len(ANSWER * 4) // 4
            yield _chunk(
                {},
                "stop",
                {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion,
                    "total_tokens": prompt_tokens + completion,
                },
            )
        yield "data: [DONE]\n\n"

    response = Response(stream(), content_type="text/event-stream")
    response.timeout = None
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8790)
    config = Config()
    config.bind = [f"127.0.0.1:{parser.parse_args().port}"]
    config.accesslog = None
    asyncio.run(serve(app, config))


if __name__ == "__main__":
    main()
