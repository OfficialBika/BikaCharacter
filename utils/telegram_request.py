from __future__ import annotations

import time

from telegram.request import HTTPXRequest

from utils.performance import MetricStore, TELEGRAM_METRICS


class MetricsHTTPXRequest(HTTPXRequest):
    async def do_request(self, url, method, request_data=None, read_timeout=None, write_timeout=None, connect_timeout=None, pool_timeout=None):
        started = time.perf_counter()
        endpoint = str(url).rsplit("/", 1)[-1] or "unknown"
        try:
            result = await super().do_request(
                url,
                method,
                request_data=request_data,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
            )
            elapsed = (time.perf_counter() - started) * 1000
            metric = TELEGRAM_METRICS.setdefault(endpoint, MetricStore())
            await metric.observe(elapsed, False)
            return result
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            metric = TELEGRAM_METRICS.setdefault(endpoint, __import__("utils.performance", fromlist=["MetricStore"]).MetricStore())
            await metric.observe(elapsed, True)
            raise
