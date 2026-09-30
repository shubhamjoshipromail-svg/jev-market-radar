"""Minimal async client for TypeSafe System One (POST /v1/systemone) with backoff on 429/529."""
import asyncio
import random
import time

import httpx

from radar.config import JEV_MODEL, JEV_USD_PER_M_INPUT, TYPESAFE_API_KEY

URL = "https://api.typesafe.ai/v1/systemone"


class JevError(RuntimeError):
    pass


def client() -> httpx.AsyncClient:
    if not TYPESAFE_API_KEY:
        raise JevError("TYPESAFE_API_KEY missing: add it to the repo-root .env")
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {TYPESAFE_API_KEY}"},
        timeout=httpx.Timeout(20.0),
        limits=httpx.Limits(max_connections=50),
    )


async def ask(http: httpx.AsyncClient, state, questions: dict, retries: int = 5) -> dict:
    """Returns {"answers", "usage", "model", "latency_ms", "cost_usd"}."""
    body = {"state": state, "model": JEV_MODEL, "questions": questions}
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        r = await http.post(URL, json=body)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code in (429, 529) and attempt < retries:
            await asyncio.sleep(min(8.0, 0.5 * 2**attempt) + random.random() * 0.3)
            continue
        if r.status_code != 200:
            raise JevError(f"{r.status_code}: {r.text[:300]}")
        data = r.json()
        tokens = data.get("usage", {}).get("input_tokens", 0)
        return {
            "answers": data["answers"],
            "usage": data.get("usage", {}),
            "model": data.get("model", JEV_MODEL),
            "latency_ms": latency_ms,
            "cost_usd": tokens * JEV_USD_PER_M_INPUT / 1_000_000,
        }
    raise JevError("retries exhausted")
