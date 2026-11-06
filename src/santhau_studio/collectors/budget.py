"""Shared request/time budget for a bounded job, including retries and split windows."""
import math
import time


class RequestBudget:
    def __init__(self, requests: int, seconds: int, *, clock=time.monotonic):
        if type(requests) is not int or requests < 1 or type(seconds) is not int or seconds < 1:
            raise ValueError("Positive integer request/time budgets are required")
        self.limit, self.used, self.clock = requests, 0, clock
        self.deadline = clock() + seconds

    @property
    def remaining_seconds(self):
        return max(0.0, self.deadline - self.clock())

    def check_time(self):
        if self.remaining_seconds <= 0:
            from santhau_studio.collectors.http_client import FetchFailed
            raise FetchFailed("REQUEST_DEADLINE_EXCEEDED")

    def check(self, delay=0):
        if self.used >= self.limit or self.clock() + delay >= self.deadline:
            from santhau_studio.collectors.http_client import FetchFailed
            raise FetchFailed("REQUEST_BUDGET_EXHAUSTED")

    def remaining_pages(self, pacing_seconds):
        """Planning ceiling only; latency/retries can still exhaust the enforced cap."""
        seconds = max(0, self.deadline - self.clock())
        slots = max(0, math.ceil(seconds / pacing_seconds) - 1)
        return min(max(0, self.limit - self.used), slots)

    def consume(self):
        self.check()
        self.used += 1

    def sleep(self, seconds, sleeper):
        self.check(seconds)
        sleeper(seconds)
