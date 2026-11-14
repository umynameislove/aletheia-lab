"""Fleet arithmetic and measured mean-cost estimates, never a capacity certificate."""
from __future__ import annotations

import math

from .async_work import BLOCKING_SLOTS_PER_PROCESS, BLOCKING_WAITERS_PER_PROCESS
from .resource_admission import SOURCE_COMPLETION_BYTES_PER_PROCESS
from .settings import RuntimeSettings


def resource_budget(settings: RuntimeSettings, *, api_replicas: int = 1,
                    worker_replicas: int = 1) -> dict[str, object]:
    if any(type(value) is not int or not 1 <= value <= 128 for value in (api_replicas, worker_replicas)):
        raise ValueError('Explicit bounded replica counts are required.')
    api_processes = api_replicas * settings.api.processes
    worker_processes = worker_replicas * settings.worker.processes
    api_connections = api_processes * settings.api.database_pool_size
    worker_connections = worker_processes * settings.worker.database_pool_size
    connections = api_connections + worker_connections + settings.storage.admin_connection_reserve
    health_reserved = int(settings.api.concurrency_limit > 1)
    return {'schema_version': 'hosted-resource-budget/v1',
        'api_replicas': api_replicas, 'worker_replicas': worker_replicas,
        'api_processes': api_processes, 'worker_processes': worker_processes,
        'api_connections': api_connections, 'worker_connections': worker_connections,
        'admin_connection_reserve': settings.storage.admin_connection_reserve,
        'total_database_connections': connections, 'postgres_budget': settings.storage.postgres_connection_budget,
        'within_database_budget': connections <= settings.storage.postgres_connection_budget,
        'regular_http_slots': api_processes * (settings.api.concurrency_limit - health_reserved),
        'health_http_slots': api_processes * health_reserved,
        'api_pool_waiters': api_processes * settings.api.concurrency_limit,
        'worker_pool_waiters': worker_processes * settings.worker.job_slots_per_process,
        'blocking_calls_per_process': BLOCKING_SLOTS_PER_PROCESS, 'blocking_call_waiters': BLOCKING_WAITERS_PER_PROCESS,
        'maximum_blocking_calls': BLOCKING_SLOTS_PER_PROCESS * (api_processes + worker_processes),
        'source_completion_logical_bytes_per_process': SOURCE_COMPLETION_BYTES_PER_PROCESS,
        'source_completion_logical_bytes_fleet': api_processes * SOURCE_COMPLETION_BYTES_PER_PROCESS,
        'worker_job_slots': worker_processes * settings.worker.job_slots_per_process,
        'metadata_response_bytes': settings.api.maximum_response_bytes,
        'request_json_bytes': settings.api.maximum_json_bytes,
        'scope': 'Configured upper bounds. RSS/FD/CPU/latency and normal-load success require measurements on the declared topology; no 50k-user capacity follows from this arithmetic.'}


def measured_mean_demand(*, completed_samples: int, arrivals_per_second: float,
                         mean_wall_seconds: float, mean_cpu_seconds: float,
                         available_cpu_cores: float) -> dict[str, object]:
    values = (arrivals_per_second, mean_wall_seconds, mean_cpu_seconds, available_cpu_cores)
    if (type(completed_samples) is not int or completed_samples < 1
            or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values)
            or mean_wall_seconds <= 0 or available_cpu_cores <= 0):
        raise ValueError('Completed sample counts and finite measured mean costs are required.')
    return {'completed_samples': completed_samples,
        'offered_arrivals_per_second': arrivals_per_second,
        'mean_inflight_littles_law': arrivals_per_second * mean_wall_seconds,
        'mean_cpu_cores_demand': arrivals_per_second * mean_cpu_seconds,
        'mean_cpu_fraction': arrivals_per_second * mean_cpu_seconds / available_cpu_cores,
        'scope': 'Mean-cost estimate for this measured workload only; excludes tail latency, failed/waiting jobs, generator saturation and dependencies. It is not a normal50k pass.'}
