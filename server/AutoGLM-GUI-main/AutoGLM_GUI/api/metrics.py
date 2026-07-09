"""Prometheus metrics endpoint."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from AutoGLM_GUI.metrics import get_metrics_registry

router = APIRouter()


@router.get("/api/metrics")
def metrics_endpoint() -> Response:
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text exposition format.
    This endpoint should be scraped by Prometheus at regular intervals.

    Example Prometheus scrape config:
        scrape_configs:
          - job_name: 'autoglm-gui'
            scrape_interval: 15s
            static_configs:
              - targets: ['localhost:8000']
            metrics_path: '/api/metrics'

    Returns:
        Response: Prometheus-compatible text format
    """
    registry = get_metrics_registry()
    data = generate_latest(registry)

    return Response(
        content=data,
        media_type=CONTENT_TYPE_LATEST,
    )
