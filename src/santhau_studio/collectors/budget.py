"""Shared request/time budget for a bounded job, including retries and split windows."""
import time


class RequestBudget:
    def __init__(self, requests: int, seconds: int, *, clock=time.monotonic):
        if type(requests) is not int or requests < 1 or type(seconds) is not int or seconds < 1:
            raise ValueError("Positive integer request/time budgets are required")
        self.limit, self.used, self.clock = requests, 0, clock
        self.deadline = clock() + seconds

    def check(self, delay=0):
        if self.used >= self.limit or self.clock() + delay >= self.deadline:
            from santhau_studio.collectors.http_client import FetchFailed
            raise FetchFailed("REQUEST_BUDGET_EXHAUSTED")

    def consume(self):
        self.check()
        self.used += 1

    def sleep(self, seconds, sleeper):
        self.check(seconds)
        sleeper(seconds)
