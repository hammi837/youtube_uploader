"""
routers/content.py — AI content generation endpoints (Phase 2A).

POST   /api/content/research          — research a topic
POST   /api/content/generate-script   — research + generate full script
GET    /api/content                   — list all content projects
GET    /api/content/{content_id}      — get project detail (with script)
DELETE /api/content/{content_id}      — delete project (DB only, not YouTube)

Security:
  - Never exposes GROQ_API_KEY, OAuth tokens, or any credentials.
  - LLM/Research errors are returned as structured 4xx/5xx responses.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.content_models import (
    ContentProject,
    ContentProjectDetailResponse,
    ContentProjectResponse,
    ContentStatus,
    GeneratedScript,
    GeneratedScriptRecord,
    ResearchRequest,
    ResearchResponse,
    ResearchSourceRecord,
    ResearchSourceResponse,
    ScriptRequest,
    project_to_detail,
    project_to_summary,
)
from backend.db import get_db
from backend.services.llm.base import LLMConfigError, LLMError, LLMJSONError, LLMRateLimitError
from backend.services.research.base import (
    ResearchEmptyTopicError,
    ResearchError,
    ResearchNetworkError,
    ResearchNoSourcesError,
    ResearchRateLimitError,
)
from backend.services.research.factory import get_research_provider
from backend.services.script_generator import generate_script

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/content", tags=["content"])


# ── POST /api/content/research ────────────────────────────────────────────────

@router.post("/research", response_model=ResearchResponse)
def research_topic(
    body: ResearchRequest,
    db: Session = Depends(get_db),
) -> ResearchResponse:
    """
    Research a topic using the configured research provider.

    Returns structured research (sources, key facts, summary).
    Does NOT generate a script or persist anything to the database.
    """
    logger.info("POST /api/content/research topic='%s'", body.topic)

    try:
        provider = get_research_provider()
        result = provider.research(
            topic=body.topic,
            language=body.language,
            depth=body.depth,
            max_sources=6,
        )
    except ResearchEmptyTopicError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ResearchNoSourcesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ResearchRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Research rate limit: {exc}",
        )
    except ResearchNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Research network error: {exc}",
        )
    except ResearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Research failed: {exc}",
        )

    return ResearchResponse(
        topic=result.topic,
        sources=[
            ResearchSourceResponse(
                title=s.title,
                url=s.url,
                snippet=s.snippet,
                key_points=s.key_points,
            )
            for s in result.sources
        ],
        key_facts=result.key_facts,
        research_summary=result.research_summary,
    )


# ── POST /api/content/generate-script ────────────────────────────────────────

@router.post(
    "/generate-script",
    status_code=status.HTTP_201_CREATED,
    response_model=ContentProjectDetailResponse,
)
def generate_script_endpoint(
    body: ScriptRequest,
    db: Session = Depends(get_db),
) -> ContentProjectDetailResponse:
    """
    Research a topic and generate a full structured YouTube script.

    Steps:
        1. Create a ContentProject record (status=researching).
        2. Run research.
        3. Generate script via LLM.
        4. Validate and persist script + sources.
        5. Return the complete project detail.

    On any failure, sets project status=failed with an error message.
    """
    logger.info("POST /api/content/generate-script topic='%s'", body.topic)

    # Create project record
    project = ContentProject(
        id=str(uuid.uuid4()),
        topic=body.topic,
        language=body.language,
        tone=body.tone,
        target_duration_seconds=body.target_duration_seconds,
        scene_count=body.scene_count,
        status=ContentStatus.RESEARCHING,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    try:
        # Research + script generation
        project.status = ContentStatus.RESEARCHING
        db.commit()

        research, script = generate_script(body)

        # Update status to generating (script is validated, now persisting)
        project.status = ContentStatus.GENERATING
        db.commit()

        # Persist research sources
        for src in research.sources:
            source_record = ResearchSourceRecord(
                id=str(uuid.uuid4()),
                content_project_id=project.id,
                title=src.title,
                url=src.url,
                snippet=src.snippet,
                source_data=json.dumps(src.key_points),
            )
            db.add(source_record)

        # Persist generated script
        script_record = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title=script.title,
            description=script.description,
            hook=script.hook,
            tags_json=json.dumps(script.tags),
            scenes_json=json.dumps([s.model_dump() for s in script.scenes]),
            estimated_duration_seconds=script.estimated_duration_seconds,
        )
        db.add(script_record)

        project.status = ContentStatus.COMPLETED
        db.commit()
        db.refresh(project)

        logger.info(
            "Script generation completed: project_id=%s title='%s'",
            project.id, script.title,
        )

        return project_to_detail(project)

    except (ResearchEmptyTopicError, ValueError) as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ResearchNoSourcesError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ResearchRateLimitError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Research rate limit: {exc}",
        )
    except ResearchNetworkError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Research network error: {exc}",
        )
    except ResearchError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Research failed: {exc}",
        )
    except LLMConfigError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except LLMRateLimitError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )
    except LLMJSONError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except LLMError as exc:
        _fail_project(project, db, str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM error: {exc}",
        )
    except Exception as exc:
        logger.exception("Unexpected error during script generation for project %s", project.id)
        _fail_project(project, db, f"Unexpected error: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during script generation.",
        )


# ── GET /api/content ──────────────────────────────────────────────────────────

@router.get("", response_model=list[ContentProjectResponse])
def list_content_projects(
    db: Session = Depends(get_db),
) -> list[ContentProjectResponse]:
    """Return all content projects, newest first."""
    projects = (
        db.query(ContentProject)
        .order_by(ContentProject.created_at.desc())
        .all()
    )
    return [project_to_summary(p) for p in projects]


# ── GET /api/content/{content_id} ────────────────────────────────────────────

@router.get("/{content_id}", response_model=ContentProjectDetailResponse)
def get_content_project(
    content_id: str,
    db: Session = Depends(get_db),
) -> ContentProjectDetailResponse:
    """Return a content project with its research sources and generated script."""
    project = _get_project_or_404(content_id, db)
    return project_to_detail(project)


# ── DELETE /api/content/{content_id} ─────────────────────────────────────────

@router.delete("/{content_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_content_project(
    content_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete a content project and all its associated research/scripts.

    This does NOT delete any YouTube videos.
    """
    project = _get_project_or_404(content_id, db)
    db.delete(project)
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_project_or_404(content_id: str, db: Session) -> ContentProject:
    project = db.query(ContentProject).filter(ContentProject.id == content_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Content project not found: {content_id}",
        )
    return project


def _fail_project(project: ContentProject, db: Session, message: str) -> None:
    """Mark a project as failed without leaking sensitive info."""
    try:
        project.status = ContentStatus.FAILED
        # Never include API keys or credentials in error messages
        safe_message = message[:500] if message else "Unknown error"
        project.error_message = safe_message
        db.commit()
    except Exception:
        pass  # Don't mask the original exception
