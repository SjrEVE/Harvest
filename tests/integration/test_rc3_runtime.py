import importlib.util
from pathlib import Path

import httpx
import pytest

from santhau_studio.collectors.http_client import FetchFailed, FetchLane
from santhau_studio.collectors.network_policy import RetryPolicy
from santhau_studio.workers.jobs import JobError, JobRequest, WorkerLoop


def test_last_network_failure_keeps_each_attempt():
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(503))) as client:
        lane = FetchLane(url='https://muasamcong.mof.gov.vn/test', source_key='egp_home',client=client,
                         retry=RetryPolicy(max_attempts=2),sleeper=lambda _:None)
        with pytest.raises(FetchFailed) as error:
            lane.post({})
        assert len(error.value.attempt_log) == 2
        assert error.value.attempt_log[-1].http_status == 503


def test_wrong_job_command_does_not_pause_current(tmp_path):
    loop = WorkerLoop(tmp_path)
    loop._current = JobRequest('right','BACKFILL','test',(),{},created_at='2026-11-01T00:00:00Z')
    with pytest.raises(JobError, match='TARGET'):
        loop.post_command(job_id='wrong', command='PAUSE', sid='test')
    assert not loop.flag.interrupt_requested


def test_invalid_budget_is_rejected():
    with pytest.raises(JobError, match='max_pages'):
        JobRequest('budget','BACKFILL','test',(),{},request_limits={'max_pages':0},created_at='2026-11-01T00:00:00Z')


def test_canary_execute_is_parsed_but_owner_required(monkeypatch,tmp_path):
    spec = importlib.util.spec_from_file_location('canary_cli',Path(__file__).parents[2]/'tools/canary.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv('SANTHAU_CANARY_APPROVED',raising=False)
    assert module.main(['probe','--family','TBMT','--data-root',str(tmp_path),'--execute']) == 2
    assert not (tmp_path/'warehouse.sqlite').exists()
