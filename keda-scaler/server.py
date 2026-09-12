"""KEDA external scaler for the migration-worker Deployment.

Implements KEDA's `ExternalScaler` gRPC contract (externalscaler.proto,
copied verbatim from KEDA's own repo) so a `ScaledObject` of type
`external-grpc` can scale `migration-worker` on Temporal's own
`approximate_backlog_count` for the "file-migration" task queue,
instead of CPU -- the metric that actually reflects "is there work
waiting for a worker", queried straight from Temporal's
DescribeTaskQueue API. No Prometheus, no custom metrics adapter: KEDA
calls this service directly.

GetMetrics/IsActive both use the same backlog query so they can never
disagree with each other about whether there's work waiting.
"""

from __future__ import annotations

import asyncio
import logging
import os

import externalscaler_pb2 as pb2
import externalscaler_pb2_grpc as pb2_grpc
import grpc
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("keda_temporal_scaler")

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE_NAME = os.environ.get("TASK_QUEUE", "file-migration")
# How much backlog "belongs" to one pod before KEDA should add another --
# e.g. 50 means: 200 backlogged tasks -> ~4 desired replicas. Tune this
# against real activity duration/throughput, not guessed once and left.
TARGET_BACKLOG_PER_POD = float(os.environ.get("TARGET_BACKLOG_PER_POD", "50"))
GRPC_PORT = os.environ.get("GRPC_PORT", "6000")
METRIC_NAME = "temporalTaskQueueBacklog"


async def _current_backlog(client: Client) -> int:
    """Sum of approximate_backlog_count across the workflow and activity
    task queues -- our worker polls both for TASK_QUEUE_NAME, and either
    one backing up means more replicas would help drain it."""
    total = 0
    for tq_type in (TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY):
        request = DescribeTaskQueueRequest(
            namespace=TEMPORAL_NAMESPACE,
            task_queue=TaskQueue(name=TASK_QUEUE_NAME),
            task_queue_type=tq_type,
            report_stats=True,
        )
        response = await client.workflow_service.describe_task_queue(request)
        total += response.stats.approximate_backlog_count
    return total


class TemporalBacklogScaler(pb2_grpc.ExternalScalerServicer):
    def __init__(self, client: Client):
        self._client = client

    async def IsActive(self, request, context):
        backlog = await _current_backlog(self._client)
        logger.info("IsActive: backlog=%d", backlog)
        return pb2.IsActiveResponse(result=backlog > 0)

    async def GetMetricSpec(self, request, context):
        return pb2.GetMetricSpecResponse(
            metricSpecs=[
                pb2.MetricSpec(metricName=METRIC_NAME, targetSizeFloat=TARGET_BACKLOG_PER_POD)
            ]
        )

    async def GetMetrics(self, request, context):
        backlog = await _current_backlog(self._client)
        logger.info("GetMetrics: backlog=%d", backlog)
        return pb2.GetMetricsResponse(
            metricValues=[
                pb2.MetricValue(metricName=request.metricName, metricValueFloat=float(backlog))
            ]
        )


async def serve() -> None:
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    logger.info(
        "Connected to Temporal at %s, watching task queue %r (target backlog/pod=%s)",
        TEMPORAL_ADDRESS,
        TASK_QUEUE_NAME,
        TARGET_BACKLOG_PER_POD,
    )

    server = grpc.aio.server()
    pb2_grpc.add_ExternalScalerServicer_to_server(TemporalBacklogScaler(client), server)
    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    logger.info("KEDA external scaler listening on :%s", GRPC_PORT)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
