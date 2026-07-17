"""
压测引擎：使用 asyncio + httpx 模拟多用户并发请求 /chat 接口，
收集每个请求的耗时、成功/失败状态，计算 QPS、延迟分位数等指标。

设计要点：
- 服务端发起请求（绕过浏览器 6 并发限制）
- 先登录获取 admin token，所有压测请求带 token
- 提问内容从传入列表轮询分配，模拟不同用户问不同问题
"""
import time
import asyncio
import httpx
from app.logger import get_logger

bench_log = get_logger("benchmark")


def _percentile(sorted_values: list[float], p: float) -> float:
    """计算第 p 百分位数（线性插值），p 取值范围 0-100"""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] * (1 - c) + sorted_values[f + 1] * c
    return sorted_values[f]


async def _login_and_get_token(base_url: str) -> str:
    """用管理员账号登录，获取 JWT token"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/auth/login",
            json={"username": "admin", "password": "123456"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["access_token"]


async def _send_one_request(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    message: str,
    index: int,
) -> dict:
    """发送单次 /chat 请求，返回计时结果"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"message": message, "enable_rag": True}

    start = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=120.0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        resp.read()  # 确保响应体被消费
        return {
            "index": index,
            "message": message[:40],
            "status_code": resp.status_code,
            "elapsed_ms": round(elapsed_ms, 1),
            "response_length": len(resp.content),
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "index": index,
            "message": message[:40],
            "status_code": 0,
            "elapsed_ms": round(elapsed_ms, 1),
            "response_length": 0,
            "error": type(e).__name__ + ": " + str(e)[:100],
        }


async def run_benchmark(
    num_users: int,
    messages: list[str],
    base_url: str,
) -> dict:
    """
    执行压测并返回汇总结果（批处理模式，等所有请求完成后返回）。

    Args:
        num_users: 并发用户数
        messages: 提问内容列表（不足时循环使用）
        base_url: 本地服务地址，如 http://127.0.0.1:8000

    Returns:
        dict，包含 total_requests / success_count / error_count / qps /
        total_duration_ms / latency / errors / per_request
    """
    processed = []
    async for event in run_benchmark_stream(num_users, messages, base_url):
        if event["type"] == "tick":
            processed.append(event["result"])
        elif event["type"] == "summary":
            return event


async def run_benchmark_stream(
    num_users: int,
    messages: list[str],
    base_url: str,
):
    """
    流式压测：使用 asyncio.as_completed 完成一个请求就推送一个结果。

    每完成一个请求，yield {"type": "tick", "result": {...}, "completed": N, "total": M}
    全部完成后，yield {"type": "summary", "total_requests": ..., "qps": ..., ...}
    """
    bench_log.info(
        f"开始流式压测: num_users={num_users} messages={len(messages)} base_url={base_url}"
    )

    # 1. 登录获取 token
    token = await _login_and_get_token(base_url)
    bench_log.info("压测用 token 获取成功")

    # 2. 准备请求协程
    chat_url = f"{base_url}/chat"
    all_results = []
    tasks = []

    limits = httpx.Limits(max_connections=num_users + 10, max_keepalive_connections=num_users)
    async with httpx.AsyncClient(limits=limits) as client:
        overall_start = time.perf_counter()

        # 创建所有协程任务
        for i in range(num_users):
            msg = messages[i % len(messages)]
            tasks.append(_send_one_request(client, chat_url, token, msg, i))

        # 3. 逐个完成即推送（as_completed 而非 gather）
        completed_count = 0
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
            except Exception as e:
                result = {
                    "index": -1,
                    "message": "",
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "response_length": 0,
                    "error": type(e).__name__ + ": " + str(e)[:100],
                }
            completed_count += 1
            all_results.append(result)

            yield {
                "type": "tick",
                "result": result,
                "completed": completed_count,
                "total": num_users,
            }

        overall_elapsed_ms = (time.perf_counter() - overall_start) * 1000

    # 4. 汇总统计
    all_results.sort(key=lambda x: x["index"])
    success_results = [r for r in all_results if r["status_code"] == 200]
    error_results = [r for r in all_results if r["status_code"] != 200]
    latencies = sorted([r["elapsed_ms"] for r in success_results])

    total_requests = len(all_results)
    success_count = len(success_results)
    error_count = len(error_results)
    qps = success_count / (overall_elapsed_ms / 1000) if overall_elapsed_ms > 0 else 0

    latency_stats = {
        "min_ms": round(latencies[0], 1) if latencies else 0,
        "max_ms": round(latencies[-1], 1) if latencies else 0,
        "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "p50_ms": round(_percentile(latencies, 50), 1),
        "p75_ms": round(_percentile(latencies, 75), 1),
        "p90_ms": round(_percentile(latencies, 90), 1),
        "p95_ms": round(_percentile(latencies, 95), 1),
        "p99_ms": round(_percentile(latencies, 99), 1),
    }

    errors = [r["error"] for r in error_results if r["error"]][:10]

    bench_log.info(
        f"流式压测完成: total={total_requests} ok={success_count} err={error_count} "
        f"qps={qps:.1f} avg={latency_stats['avg_ms']}ms p95={latency_stats['p95_ms']}ms"
    )

    yield {
        "type": "summary",
        "total_requests": total_requests,
        "success_count": success_count,
        "error_count": error_count,
        "qps": round(qps, 2),
        "total_duration_ms": round(overall_elapsed_ms, 1),
        "latency": latency_stats,
        "errors": errors,
        "per_request": all_results,
    }
