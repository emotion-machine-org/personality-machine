import asyncio
import logging
import time
from functools import wraps


def log_execution_time(fn):
    if asyncio.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            log = logging.getLogger(fn.__module__)
            start_time = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                end_time = time.perf_counter()
                total_time_ms = (end_time - start_time) * 1000
                log.info("Function %s took %.3f ms", fn.__name__, total_time_ms)

        return async_wrapper
    else:

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            log = logging.getLogger(fn.__module__)
            start_time = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                end_time = time.perf_counter()
                total_time_ms = (end_time - start_time) * 1000
                log.info("Function %s took %.3f ms", fn.__name__, total_time_ms)

        return sync_wrapper
