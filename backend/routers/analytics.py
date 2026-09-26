"""
routers/analytics.py — Phase 3K analytics endpoints.

All endpoints are read-only and return analytics data based on truthful,
available data from the queue job records.

Endpoints:
  GET /api/analytics/summary — Summary metrics
  GET /api/analytics/performance — Performance metrics
  GET /api/analytics/errors — Error aggregation
  GET /api/analytics/resources — Current resource usage
  GET /api/analytics/jobs — Queryable job analytics

Security: No credentials, no tokens, no filesystem paths exposed.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from backend.analytics_models import (
    AnalyticsJobsResponse,
    AnalyticsSummary,
    ErrorAggregation,
    PerformanceMetrics,
    ResourceUsage,
)
from backend.services.analytics import (
    aggregate_errors,
    compute_current_resource_usage,
    compute_performance_metrics,
    compute_summary_metrics,
    query_jobs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ── GET /api/analytics/summary ─────────────────────────────────────────────────


@router.get("/summary", response_model=AnalyticsSummary)
def get_summary(days: str = Query("7", description="Date range: '7', '30', or 'all'")):
    """
    Return summary metrics for the specified date range.

    Metrics include:
    - Total jobs, completed jobs, failed jobs
    - Success rate
    - Average total duration
    - Uploads today
    - Current disk usage
    """
    try:
        return compute_summary_metrics(days)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Analytics summary query failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics temporarily unavailable",
        )


# ── GET /api/analytics/performance ───────────────────────────────────────────


@router.get("/performance", response_model=PerformanceMetrics)
def get_performance(days: str = Query("7", description="Date range: '7', '30', or 'all'")):
    """
    Return performance metrics for the specified date range.

    Metrics include:
    - Jobs completed, jobs with retries
    - Retry rate
    - Average total duration
    - P50/P90/P95 duration percentiles
    """
    try:
        return compute_performance_metrics(days)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Analytics performance query failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics temporarily unavailable",
        )


# ── GET /api/analytics/errors ───────────────────────────────────────────────


@router.get("/errors", response_model=ErrorAggregation)
def get_errors(days: str = Query("7", description="Date range: '7', '30', or 'all'")):
    """
    Return error aggregation for the specified date range.

    Metrics include:
    - Total errors, transient errors, permanent errors, unknown errors
    - Error breakdown by type
    - Error breakdown by stage
    - Top error messages
    """
    try:
        return aggregate_errors(days)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Analytics error aggregation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics temporarily unavailable",
        )


# ── GET /api/analytics/resources ────────────────────────────────────────────


@router.get("/resources", response_model=ResourceUsage)
def get_resources():
    """
    Return current resource usage snapshots.

    Metrics include:
    - Current disk usage (snapshot, not historical trend)
    - Free disk space
    - Temp directory size
    - Audio storage size
    - Video storage size
    - AI images storage size
    """
    try:
        return compute_current_resource_usage()
    except Exception as e:
        logger.error("Analytics resource usage query failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics temporarily unavailable",
        )


# ── GET /api/analytics/jobs ────────────────────────────────────────────────


@router.get("/jobs", response_model=AnalyticsJobsResponse)
def get_jobs(
    days: str = Query("7", description="Date range: '7', '30', or 'all'"),
    status: Optional[str] = Query(None, description="Filter by queue status"),
    language: Optional[str] = Query(None, description="Filter by language"),
    page: int = Query(1, description="Page number (1-based)", ge=1),
    page_size: int = Query(50, description="Items per page", ge=1, le=200),
):
    """
    Return paginated job analytics with optional filters.

    Filters:
    - days: Date range
    - status: Filter by queue status
    - language: Filter by language
    - page: Page number
    - page_size: Items per page (max 200)
    """
    try:
        return query_jobs(days=days, status=status, language=language, page=page, page_size=page_size)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Analytics jobs query failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics temporarily unavailable",
        )
