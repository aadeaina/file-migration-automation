"""Unit tests for the scaler's own logic -- no live Temporal server, a
fake `workflow_service.describe_task_queue` standing in for one.
Verified against a real cluster separately (see README.md's "Verified
locally" section); this covers the gRPC servicer contract itself,
which is easy to get subtly wrong (metric name mismatches between
GetMetricSpec/GetMetrics, wrong response type) without a full
integration run.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import server as scaler_server


class FakeWorkflowService:
    def __init__(self, backlog_by_type: dict[int, int]):
        self._backlog_by_type = backlog_by_type

    async def describe_task_queue(self, request):
        count = self._backlog_by_type.get(request.task_queue_type, 0)
        return SimpleNamespace(stats=SimpleNamespace(approximate_backlog_count=count))


class FakeClient:
    def __init__(self, backlog_by_type: dict[int, int]):
        self.workflow_service = FakeWorkflowService(backlog_by_type)


def test_current_backlog_sums_workflow_and_activity_queues():
    client = FakeClient({1: 3, 2: 7})  # WORKFLOW=1, ACTIVITY=2

    backlog = asyncio.run(scaler_server._current_backlog(client))

    assert backlog == 10


def test_current_backlog_is_zero_when_queues_are_empty():
    client = FakeClient({})

    backlog = asyncio.run(scaler_server._current_backlog(client))

    assert backlog == 0


def test_is_active_reports_true_when_backlog_exists():
    scaler = scaler_server.TemporalBacklogScaler(FakeClient({1: 1}))

    response = asyncio.run(scaler.IsActive(None, None))

    assert response.result is True


def test_is_active_reports_false_when_no_backlog():
    scaler = scaler_server.TemporalBacklogScaler(FakeClient({}))

    response = asyncio.run(scaler.IsActive(None, None))

    assert response.result is False


def test_get_metric_spec_uses_configured_target():
    scaler = scaler_server.TemporalBacklogScaler(FakeClient({}))

    response = asyncio.run(scaler.GetMetricSpec(None, None))

    assert len(response.metricSpecs) == 1
    assert response.metricSpecs[0].metricName == scaler_server.METRIC_NAME
    assert response.metricSpecs[0].targetSizeFloat == scaler_server.TARGET_BACKLOG_PER_POD


def test_get_metrics_returns_the_same_backlog_is_active_would_see():
    client = FakeClient({1: 4, 2: 6})
    scaler = scaler_server.TemporalBacklogScaler(client)
    request = SimpleNamespace(metricName=scaler_server.METRIC_NAME)

    response = asyncio.run(scaler.GetMetrics(request, None))

    assert len(response.metricValues) == 1
    assert response.metricValues[0].metricName == scaler_server.METRIC_NAME
    assert response.metricValues[0].metricValueFloat == 10.0
