import argparse
import asyncio
import math
import statistics
import time

import httpx


async def benchmark_endpoint(
    name: str,
    request,
    rate: int,
    duration: int,
) -> dict:
    latencies: list[float] = []
    errors = 0
    observed = 0
    skipped = 0
    max_in_flight = max(64, rate * 2)
    tasks: set[asyncio.Task] = set()
    started = time.perf_counter()
    deadline = started + duration

    async def measure():
        nonlocal errors, observed
        begin = time.perf_counter()
        try:
            response = await request()
            if response.status_code >= 400:
                errors += 1
            latencies.append(time.perf_counter() - begin)
        except httpx.HTTPError:
            errors += 1
            latencies.append(time.perf_counter() - begin)
        observed += 1

    sequence = 0
    while True:
        scheduled = started + sequence / rate
        remaining = scheduled - time.perf_counter()
        if remaining > 0:
            await asyncio.sleep(remaining)
        if time.perf_counter() >= deadline:
            break
        finished = {task for task in tasks if task.done()}
        if finished:
            await asyncio.gather(*finished)
            tasks.difference_update(finished)
        if len(tasks) >= max_in_flight:
            skipped += 1
        else:
            tasks.add(asyncio.create_task(measure()))
        sequence += 1

    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started
    latencies.sort()

    def percentile(value: float) -> float:
        if not latencies:
            return 0.0
        return latencies[max(0, math.ceil(value * len(latencies)) - 1)] * 1000

    return {
        "endpoint": name,
        "target_rps": rate,
        "duration_seconds": round(elapsed, 2),
        "completed_requests": observed,
        "successful_requests": observed - errors,
        "errors": errors,
        "client_skipped": skipped,
        "achieved_rps": round((observed - errors) / elapsed, 2),
        "p50_ms": round(statistics.median(latencies) * 1000, 2) if latencies else 0,
        "p95_ms": round(percentile(0.95), 2),
        "p99_ms": round(percentile(0.99), 2),
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Measure URL shortener creation, redirect, and stats endpoints."
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--rates", nargs="+", type=int, default=[25, 50, 100])
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()
    if args.duration < 1 or any(rate < 1 for rate in args.rates):
        parser.error("duration and request rates must be positive")

    max_connections = max(100, max(args.rates) * 2)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
        ),
        timeout=30,
    ) as client:
        created = await client.post(
            "/api/shorten",
            json={"target_url": "https://example.com/benchmark"},
        )
        created.raise_for_status()
        code = created.json()["short_code"]
        scenarios = [
            (
                "create",
                lambda: client.post(
                    "/api/shorten",
                    json={"target_url": "https://example.com/benchmark"},
                ),
            ),
            ("redirect", lambda: client.get(f"/{code}")),
            ("stats", lambda: client.get(f"/api/urls/{code}/stats")),
        ]
        for rate in args.rates:
            for name, request in scenarios:
                result = await benchmark_endpoint(name, request, rate, args.duration)
                print(result)


if __name__ == "__main__":
    asyncio.run(main())
