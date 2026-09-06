"""
tests/test_content_api.py — Tests for Phase 2A: AI Research + Script Generation.

Tests:
  Research:
    - valid topic returns 200 with sources
    - empty topic returns 422
    - network failure returns 503
    - provider error returns 500

  LLM / Script generation:
    - provider selection (groq / local / unknown)
    - missing API key returns 503 with clear message
    - mocked successful generation returns 201 with full structure
    - rate limit returns 429
    - malformed LLM JSON returns 422
    - schema validation (title length, tags, scenes)

  API endpoints:
    - POST /api/content/research
    - POST /api/content/generate-script
    - GET /api/content (history)
    - GET /api/content/{id}
    - DELETE /api/content/{id}

  Security:
    - GROQ_API_KEY never appears in any response body
    - OAuth tokens never appear in any response body

IMPORTANT: No real external API calls are made.
All external calls are mocked.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import Base, get_db
from backend.main import app
from backend.content_models import (
    ContentProject,
    ContentStatus,
    GeneratedScript,
    GeneratedScriptRecord,
    ResearchSourceRecord,
    Scene,
)

# ── Test database ──────────────────────────────────────────────────────────────
# Use the shared engine managed by conftest.py so both test files share the
# same StaticPool connection and the app override stays consistent.
from tests.conftest import shared_engine, SharedTestingSessionLocal

test_engine = shared_engine
TestingSessionLocal = SharedTestingSessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db



@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── Shared mock data ──────────────────────────────────────────────────────────

def _mock_research_result(topic: str = "test topic"):
    """Build a mock ResearchResult dataclass."""
    from backend.services.research.base import ResearchResult, ResearchSource
    return ResearchResult(
        topic=topic,
        sources=[
            ResearchSource(
                title="Test Source",
                url="https://example.com/test",
                snippet="This is a test snippet about the topic.",
                key_points=["Fact one", "Fact two"],
            )
        ],
        key_facts=["Key fact one", "Key fact two"],
        research_summary=f"Research summary for {topic}.",
    )


def _mock_script_json(topic: str = "test topic") -> dict:
    """Build a valid LLM JSON response for script generation."""
    return {
        "title": f"Amazing Facts About {topic}"[:100],
        "description": "A detailed description of the video content.",
        "tags": ["topic", "facts", "education"],
        "hook": "Did you know this topic is incredibly fascinating?",
        "estimated_duration_seconds": 180,
        "scenes": [
            {
                "scene_number": 1,
                "narration": "Welcome to this video about the topic.",
                "visual_description": "Aerial view of a relevant location.",
                "estimated_duration_seconds": 15,
            },
            {
                "scene_number": 2,
                "narration": "Here is the first interesting fact.",
                "visual_description": "Text overlay: FACT #1",
                "estimated_duration_seconds": 20,
            },
            {
                "scene_number": 3,
                "narration": "And here is the conclusion.",
                "visual_description": "Closing screen with title.",
                "estimated_duration_seconds": 15,
            },
        ],
    }


# ── Helper: create a project in DB ───────────────────────────────────────────

def make_project(db, **kwargs) -> ContentProject:
    defaults = dict(
        id=str(uuid.uuid4()),
        topic="Test Topic",
        language="en",
        tone="informative",
        target_duration_seconds=180,
        scene_count=10,
        status=ContentStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    project = ContentProject(**defaults)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def make_project_with_script(db, topic: str = "Test Topic") -> ContentProject:
    """Create a completed project with sources and a script."""
    project = make_project(db, topic=topic, status=ContentStatus.COMPLETED)

    source = ResearchSourceRecord(
        id=str(uuid.uuid4()),
        content_project_id=project.id,
        title="Test Source",
        url="https://example.com",
        snippet="A test snippet.",
        source_data=json.dumps(["Fact one"]),
    )
    db.add(source)

    script = GeneratedScriptRecord(
        id=str(uuid.uuid4()),
        content_project_id=project.id,
        title=f"Script: {topic}",
        description="Test description",
        hook="A great hook sentence.",
        tags_json=json.dumps(["tag1", "tag2"]),
        scenes_json=json.dumps([
            {
                "scene_number": 1,
                "narration": "Scene one narration.",
                "visual_description": "Visual description.",
                "estimated_duration_seconds": 15,
            }
        ]),
        estimated_duration_seconds=180,
    )
    db.add(script)
    db.commit()
    db.refresh(project)
    return project


# ── Research endpoint tests ───────────────────────────────────────────────────

class TestResearchEndpoint:
    def test_valid_topic_returns_200(self, client):
        mock_result = _mock_research_result("space exploration")
        with patch(
            "backend.routers.content.get_research_provider"
        ) as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.return_value = mock_result
            mock_factory.return_value = mock_provider

            r = client.post(
                "/api/content/research",
                json={"topic": "space exploration", "language": "en", "depth": "standard"},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["topic"] == "space exploration"
        assert len(body["sources"]) == 1
        assert body["sources"][0]["title"] == "Test Source"
        assert len(body["key_facts"]) == 2
        assert "research_summary" in body

    def test_empty_topic_returns_422(self, client):
        r = client.post(
            "/api/content/research",
            json={"topic": "", "language": "en", "depth": "standard"},
        )
        assert r.status_code == 422

    def test_whitespace_only_topic_returns_422(self, client):
        r = client.post(
            "/api/content/research",
            json={"topic": "   ", "language": "en", "depth": "standard"},
        )
        assert r.status_code == 422

    def test_topic_too_long_returns_422(self, client):
        r = client.post(
            "/api/content/research",
            json={"topic": "x" * 301, "language": "en", "depth": "standard"},
        )
        assert r.status_code == 422

    def test_invalid_depth_returns_422(self, client):
        r = client.post(
            "/api/content/research",
            json={"topic": "valid topic", "language": "en", "depth": "extreme"},
        )
        assert r.status_code == 422

    def test_network_failure_returns_503(self, client):
        from backend.services.research.base import ResearchNetworkError
        with patch(
            "backend.routers.content.get_research_provider"
        ) as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.side_effect = ResearchNetworkError("No network")
            mock_factory.return_value = mock_provider

            r = client.post(
                "/api/content/research",
                json={"topic": "test", "language": "en", "depth": "standard"},
            )

        assert r.status_code == 503
        assert "network" in r.json()["detail"].lower()

    def test_research_error_returns_500(self, client):
        from backend.services.research.base import ResearchError
        with patch(
            "backend.routers.content.get_research_provider"
        ) as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.side_effect = ResearchError("DDG failure")
            mock_factory.return_value = mock_provider

            r = client.post(
                "/api/content/research",
                json={"topic": "test", "language": "en", "depth": "standard"},
            )

        assert r.status_code == 500

    def test_response_never_contains_secrets(self, client):
        mock_result = _mock_research_result()
        with patch("backend.routers.content.get_research_provider") as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.return_value = mock_result
            mock_factory.return_value = mock_provider

            r = client.post(
                "/api/content/research",
                json={"topic": "test", "language": "en", "depth": "standard"},
            )

        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token" not in body


# ── LLM provider tests ────────────────────────────────────────────────────────

class TestLLMProviderFactory:
    def test_groq_selected_when_configured(self):
        from backend.services.llm.factory import get_llm_provider
        with patch.dict(os.environ, {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "test-key-123"}):
            provider = get_llm_provider()
        assert provider.provider_name == "groq"

    def test_local_selected_when_configured(self):
        from backend.services.llm.factory import get_llm_provider
        with patch.dict(os.environ, {"LLM_PROVIDER": "local"}):
            provider = get_llm_provider()
        assert provider.provider_name == "local"

    def test_unknown_provider_raises_config_error(self):
        from backend.services.llm.base import LLMConfigError
        from backend.services.llm.factory import get_llm_provider
        with patch.dict(os.environ, {"LLM_PROVIDER": "imaginary_llm"}):
            with pytest.raises(LLMConfigError):
                get_llm_provider()

    def test_groq_missing_api_key_raises_config_error(self):
        from backend.services.llm.base import LLMConfigError
        from backend.services.llm.groq_provider import GroqProvider
        with patch.dict(os.environ, {}, clear=True):
            # Ensure GROQ_API_KEY is absent
            env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(LLMConfigError, match="GROQ_API_KEY"):
                    GroqProvider()

    def test_local_provider_raises_not_implemented(self):
        from backend.services.llm.base import LLMConfigError
        from backend.services.llm.local_provider import LocalLLMProvider
        provider = LocalLLMProvider()
        with pytest.raises(LLMConfigError):
            provider.generate("test prompt")

    def test_local_provider_json_raises_not_implemented(self):
        from backend.services.llm.base import LLMConfigError
        from backend.services.llm.local_provider import LocalLLMProvider
        provider = LocalLLMProvider()
        with pytest.raises(LLMConfigError):
            provider.generate_json("test prompt")


# ── Groq provider unit tests ──────────────────────────────────────────────────

class TestGroqProviderParsing:
    """Tests for JSON parsing logic — no real HTTP calls."""

    def test_parse_clean_json(self):
        from backend.services.llm.groq_provider import _parse_json
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_with_code_fence(self):
        from backend.services.llm.groq_provider import _parse_json
        raw = '```json\n{"key": "value"}\n```'
        result = _parse_json(raw)
        assert result == {"key": "value"}

    def test_parse_json_with_plain_fence(self):
        from backend.services.llm.groq_provider import _parse_json
        raw = '```\n{"key": "value"}\n```'
        result = _parse_json(raw)
        assert result == {"key": "value"}

    def test_parse_json_with_preamble(self):
        from backend.services.llm.groq_provider import _parse_json
        raw = 'Here is your JSON:\n{"key": "value"}'
        result = _parse_json(raw)
        assert result == {"key": "value"}

    def test_malformed_json_raises_llm_json_error(self):
        from backend.services.llm.base import LLMJSONError
        from backend.services.llm.groq_provider import _parse_json
        with pytest.raises(LLMJSONError):
            _parse_json("this is not json at all")

    def test_json_array_raises_llm_json_error(self):
        from backend.services.llm.base import LLMJSONError
        from backend.services.llm.groq_provider import _parse_json
        with pytest.raises(LLMJSONError):
            _parse_json("[1, 2, 3]")


# ── Generate-script endpoint tests ────────────────────────────────────────────

class TestGenerateScriptEndpoint:
    def _post_generate(self, client, topic: str = "black holes", extra: dict | None = None):
        body = {
            "topic": topic,
            "language": "en",
            "tone": "informative",
            "target_duration_seconds": 180,
            "scene_count": 3,
        }
        if extra:
            body.update(extra)
        return client.post("/api/content/generate-script", json=body)

    def _mock_generate(self, topic: str = "black holes"):
        """Context manager that mocks the full generate_script call."""
        mock_research = _mock_research_result(topic)
        mock_script = GeneratedScript(
            title=f"Amazing Facts About {topic}"[:100],
            description="A great video description.",
            tags=["space", "science"],
            hook="Did you know black holes are incredibly fascinating?",
            estimated_duration_seconds=180,
            scenes=[
                Scene(
                    scene_number=1,
                    narration="Introduction narration.",
                    visual_description="Opening visual.",
                    estimated_duration_seconds=15,
                ),
                Scene(
                    scene_number=2,
                    narration="Main content.",
                    visual_description="Content visual.",
                    estimated_duration_seconds=30,
                ),
                Scene(
                    scene_number=3,
                    narration="Conclusion.",
                    visual_description="Closing visual.",
                    estimated_duration_seconds=15,
                ),
            ],
        )
        return patch(
            "backend.routers.content.generate_script",
            return_value=(mock_research, mock_script),
        )

    def test_successful_generation_returns_201(self, client):
        with self._mock_generate():
            r = self._post_generate(client)

        assert r.status_code == 201
        body = r.json()
        assert body["topic"] == "black holes"
        assert body["status"] == ContentStatus.COMPLETED
        assert body["script"] is not None
        assert body["script"]["title"] is not None
        assert len(body["script"]["scenes"]) == 3

    def test_response_contains_sources(self, client):
        with self._mock_generate():
            r = self._post_generate(client)

        body = r.json()
        assert len(body["sources"]) >= 1
        assert body["sources"][0]["title"] == "Test Source"

    def test_response_contains_script_fields(self, client):
        with self._mock_generate():
            r = self._post_generate(client)

        script = r.json()["script"]
        assert "title" in script
        assert "description" in script
        assert "tags" in script
        assert "hook" in script
        assert "estimated_duration_seconds" in script
        assert "scenes" in script

    def test_each_scene_has_required_fields(self, client):
        with self._mock_generate():
            r = self._post_generate(client)

        for scene in r.json()["script"]["scenes"]:
            assert "scene_number" in scene
            assert "narration" in scene
            assert "visual_description" in scene
            assert "estimated_duration_seconds" in scene

    def test_project_persisted_to_db(self, client, db):
        with self._mock_generate("persistence test"):
            r = self._post_generate(client, topic="persistence test")

        assert r.status_code == 201
        project_id = r.json()["id"]
        project = db.query(ContentProject).filter(ContentProject.id == project_id).first()
        assert project is not None
        assert project.status == ContentStatus.COMPLETED
        assert project.script is not None

    def test_empty_topic_returns_422(self, client):
        r = self._post_generate(client, topic="")
        assert r.status_code == 422

    def test_whitespace_topic_returns_422(self, client):
        r = client.post(
            "/api/content/generate-script",
            json={"topic": "   ", "language": "en", "tone": "informative",
                  "target_duration_seconds": 180, "scene_count": 3},
        )
        assert r.status_code == 422

    def test_invalid_duration_too_short_returns_422(self, client):
        r = client.post(
            "/api/content/generate-script",
            json={"topic": "valid topic", "language": "en", "tone": "informative",
                  "target_duration_seconds": 10, "scene_count": 3},
        )
        assert r.status_code == 422

    def test_invalid_scene_count_returns_422(self, client):
        r = client.post(
            "/api/content/generate-script",
            json={"topic": "valid topic", "language": "en", "tone": "informative",
                  "target_duration_seconds": 180, "scene_count": 1},
        )
        assert r.status_code == 422

    def test_missing_api_key_returns_503(self, client):
        from backend.services.llm.base import LLMConfigError
        with patch(
            "backend.routers.content.generate_script",
            side_effect=LLMConfigError("GROQ_API_KEY is not set."),
        ):
            r = self._post_generate(client)

        assert r.status_code == 503
        # The error message may contain "GROQ_API_KEY" (the env var name) — that's fine.
        # What must NEVER appear is the actual key value, OAuth tokens, or client secrets.
        body = r.text
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token" not in body

    def test_rate_limit_returns_429(self, client):
        from backend.services.llm.base import LLMRateLimitError
        with patch(
            "backend.routers.content.generate_script",
            side_effect=LLMRateLimitError("Rate limit exceeded. Retry after: 60s."),
        ):
            r = self._post_generate(client)

        assert r.status_code == 429
        assert "rate limit" in r.json()["detail"].lower()

    def test_malformed_json_from_llm_returns_422(self, client):
        from backend.services.llm.base import LLMJSONError
        with patch(
            "backend.routers.content.generate_script",
            side_effect=LLMJSONError("Model returned malformed JSON."),
        ):
            r = self._post_generate(client)

        assert r.status_code == 422

    def test_network_error_returns_503(self, client):
        from backend.services.research.base import ResearchNetworkError
        with patch(
            "backend.routers.content.generate_script",
            side_effect=ResearchNetworkError("No network"),
        ):
            r = self._post_generate(client)

        assert r.status_code == 503

    def test_failed_project_saved_to_db(self, client, db):
        from backend.services.llm.base import LLMRateLimitError
        with patch(
            "backend.routers.content.generate_script",
            side_effect=LLMRateLimitError("Rate limit exceeded."),
        ):
            r = self._post_generate(client)

        assert r.status_code == 429
        # A failed project should still be in the DB
        projects = db.query(ContentProject).all()
        assert len(projects) == 1
        assert projects[0].status == ContentStatus.FAILED

    def test_response_never_contains_api_key(self, client):
        with self._mock_generate():
            r = self._post_generate(client)

        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token" not in body


# ── Script validation tests ───────────────────────────────────────────────────

class TestScriptValidation:
    def test_title_truncated_to_100_chars(self):
        from backend.services.script_generator import _validate_script
        from backend.content_models import ScriptRequest
        request = ScriptRequest(topic="test", scene_count=3, target_duration_seconds=60)
        raw = _mock_script_json("test")
        raw["title"] = "A" * 150  # over 100 chars
        raw["scenes"] = [
            {"scene_number": 1, "narration": "narration", "visual_description": "visual",
             "estimated_duration_seconds": 10}
        ]
        script = _validate_script(raw, request)
        assert len(script.title) <= 100

    def test_tags_capped_at_30(self):
        from backend.services.script_generator import _validate_script
        from backend.content_models import ScriptRequest
        request = ScriptRequest(topic="test", scene_count=3, target_duration_seconds=60)
        raw = _mock_script_json("test")
        raw["tags"] = [f"tag{i}" for i in range(50)]
        raw["scenes"] = [
            {"scene_number": 1, "narration": "narration", "visual_description": "visual",
             "estimated_duration_seconds": 10}
        ]
        script = _validate_script(raw, request)
        assert len(script.tags) <= 30

    def test_missing_scenes_raises_json_error(self):
        from backend.services.llm.base import LLMJSONError
        from backend.services.script_generator import _validate_script
        from backend.content_models import ScriptRequest
        request = ScriptRequest(topic="test", scene_count=3, target_duration_seconds=60)
        raw = {"title": "Test Title", "tags": [], "scenes": []}
        with pytest.raises(LLMJSONError):
            _validate_script(raw, request)

    def test_empty_narration_scenes_are_skipped(self):
        from backend.services.script_generator import _validate_script
        from backend.content_models import ScriptRequest
        request = ScriptRequest(topic="test", scene_count=3, target_duration_seconds=60)
        raw = _mock_script_json("test")
        raw["scenes"] = [
            {"scene_number": 1, "narration": "", "visual_description": "visual",
             "estimated_duration_seconds": 10},
            {"scene_number": 2, "narration": "Valid narration.", "visual_description": "visual",
             "estimated_duration_seconds": 10},
        ]
        script = _validate_script(raw, request)
        assert len(script.scenes) == 1
        assert script.scenes[0].scene_number == 2


# ── Content history endpoint tests ───────────────────────────────────────────

class TestContentHistory:
    def test_empty_history_returns_empty_list(self, client):
        r = client.get("/api/content")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_all_projects(self, client, db):
        make_project(db, topic="Topic A")
        make_project(db, topic="Topic B")
        r = client.get("/api/content")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_projects_ordered_newest_first(self, client, db):
        from datetime import timedelta
        old = make_project(
            db, topic="Old",
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        new = make_project(db, topic="New")
        r = client.get("/api/content")
        items = r.json()
        assert items[0]["topic"] == "New"
        assert items[1]["topic"] == "Old"

    def test_summary_has_correct_fields(self, client, db):
        make_project_with_script(db, "Test Topic")
        r = client.get("/api/content")
        item = r.json()[0]
        assert "id" in item
        assert "topic" in item
        assert "status" in item
        assert "has_script" in item
        assert item["has_script"] is True

    def test_history_no_secrets(self, client, db):
        make_project(db)
        body = client.get("/api/content").text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body


# ── Get content detail tests ──────────────────────────────────────────────────

class TestGetContentDetail:
    def test_returns_project_detail(self, client, db):
        project = make_project_with_script(db, "Detail Topic")
        r = client.get(f"/api/content/{project.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["topic"] == "Detail Topic"
        assert body["script"] is not None
        assert body["script"]["title"] is not None
        assert len(body["sources"]) >= 1

    def test_not_found_returns_404(self, client):
        r = client.get("/api/content/does-not-exist")
        assert r.status_code == 404

    def test_detail_includes_scenes(self, client, db):
        project = make_project_with_script(db)
        r = client.get(f"/api/content/{project.id}")
        scenes = r.json()["script"]["scenes"]
        assert len(scenes) >= 1
        assert "narration" in scenes[0]
        assert "visual_description" in scenes[0]

    def test_detail_no_secrets(self, client, db):
        project = make_project_with_script(db)
        body = client.get(f"/api/content/{project.id}").text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body


# ── Delete content tests ──────────────────────────────────────────────────────

class TestDeleteContent:
    def test_delete_returns_204(self, client, db):
        project = make_project(db)
        r = client.delete(f"/api/content/{project.id}")
        assert r.status_code == 204

    def test_deleted_project_not_found(self, client, db):
        project = make_project(db)
        project_id = project.id
        client.delete(f"/api/content/{project_id}")
        r = client.get(f"/api/content/{project_id}")
        assert r.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/content/does-not-exist")
        assert r.status_code == 404

    def test_delete_also_removes_script(self, client, db):
        project = make_project_with_script(db)
        project_id = project.id
        client.delete(f"/api/content/{project_id}")
        # Script and sources should be cascade-deleted
        scripts = db.query(GeneratedScriptRecord).filter(
            GeneratedScriptRecord.content_project_id == project_id
        ).all()
        assert len(scripts) == 0
        sources = db.query(ResearchSourceRecord).filter(
            ResearchSourceRecord.content_project_id == project_id
        ).all()
        assert len(sources) == 0


# ── Web research provider unit tests ─────────────────────────────────────────

class TestWebResearchProvider:
    """Tests for the duckduckgo-search based WebResearchProvider."""

    def _make_ddg_results(self, n: int = 2) -> list[dict]:
        """Produce fake DDGS.text() results."""
        return [
            {
                "title": f"Source {i}",
                "href": f"https://example.com/article-{i}",
                "body": f"This is a detailed snippet about the topic number {i}. "
                        f"It contains enough text to be useful for research purposes.",
            }
            for i in range(1, n + 1)
        ]

    def _patch_ddgs(self, results: list[dict]):
        """Patch DDGS().text() to return given results."""
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = iter(results)
        return patch("backend.services.research.web_provider.DDGS", return_value=mock_ddgs)

    # ── Input validation ──────────────────────────────────────────────────

    def test_empty_topic_raises_error(self):
        from backend.services.research.base import ResearchEmptyTopicError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with pytest.raises(ResearchEmptyTopicError):
            provider.research("")

    def test_whitespace_topic_raises_error(self):
        from backend.services.research.base import ResearchEmptyTopicError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with pytest.raises(ResearchEmptyTopicError):
            provider.research("   ")

    def test_topic_too_long_raises_error(self):
        from backend.services.research.base import ResearchEmptyTopicError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with pytest.raises(ResearchEmptyTopicError):
            provider.research("x" * 301)

    # ── Successful search ─────────────────────────────────────────────────

    def test_successful_search_returns_sources(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with self._patch_ddgs(self._make_ddg_results(3)):
            result = provider.research("why do cats purr")
        assert result.topic == "why do cats purr"
        assert len(result.sources) >= 1
        assert result.sources[0].title == "Source 1"
        assert "https://example.com" in result.sources[0].url

    def test_sources_have_all_fields(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with self._patch_ddgs(self._make_ddg_results(2)):
            result = provider.research("black holes")
        for src in result.sources:
            assert src.title
            assert src.url
            assert src.snippet
            assert isinstance(src.key_points, list)

    def test_key_facts_extracted(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with self._patch_ddgs(self._make_ddg_results(3)):
            result = provider.research("space facts")
        assert isinstance(result.key_facts, list)
        assert isinstance(result.research_summary, str)
        assert len(result.research_summary) > 0

    def test_max_sources_respected(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        # Provide 10 results but request max 3
        with self._patch_ddgs(self._make_ddg_results(10)):
            result = provider.research("sky is blue", max_sources=3)
        assert len(result.sources) <= 3

    # ── Zero results → ResearchNoSourcesError ─────────────────────────────

    def test_zero_results_raises_no_sources_error(self):
        from backend.services.research.base import ResearchNoSourcesError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with patch.dict(os.environ, {"RESEARCH_MAX_QUERIES": "1"}):
            with self._patch_ddgs([]):
                with pytest.raises(ResearchNoSourcesError) as exc_info:
                    provider.research("xyzzy_completely_unknown_topic_abc123")
        assert "no reliable web sources" in str(exc_info.value).lower()

    def test_zero_results_never_returns_empty_sources_list(self):
        """Provider must raise, not return a result with empty sources."""
        from backend.services.research.base import ResearchNoSourcesError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with patch.dict(os.environ, {"RESEARCH_MAX_QUERIES": "1"}):
            with self._patch_ddgs([]):
                with pytest.raises(ResearchNoSourcesError):
                    provider.research("nonexistent topic xyzzy")

    # ── Network errors ────────────────────────────────────────────────────

    def test_network_error_raises_research_network_error(self):
        from backend.services.research.base import ResearchNetworkError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("timeout connecting to server")
        with patch("backend.services.research.web_provider.DDGS", return_value=mock_ddgs):
            with pytest.raises(ResearchNetworkError):
                provider.research("black holes")

    def test_connection_refused_raises_research_network_error(self):
        from backend.services.research.base import ResearchNetworkError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("connection refused to network host")
        with patch("backend.services.research.web_provider.DDGS", return_value=mock_ddgs):
            with pytest.raises(ResearchNetworkError):
                provider.research("black holes")

    def test_rate_limit_raises_rate_limit_error(self):
        from backend.services.research.base import ResearchRateLimitError
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        # Simulate the actual RatelimitException from duckduckgo_search
        from duckduckgo_search.exceptions import RatelimitException
        mock_ddgs.text.side_effect = RatelimitException("202 Ratelimit")
        with patch("backend.services.research.web_provider.DDGS", return_value=mock_ddgs):
            with pytest.raises(ResearchRateLimitError):
                provider.research("black holes")

    # ── Deduplication ─────────────────────────────────────────────────────

    def test_duplicate_urls_are_deduplicated(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        # Two results with the same URL
        dupes = [
            {"title": "Source A", "href": "https://example.com/same",
             "body": "Some long enough snippet text here for testing."},
            {"title": "Source B", "href": "https://example.com/same",
             "body": "Another long enough snippet text here for testing."},
            {"title": "Source C", "href": "https://example.com/different",
             "body": "A third long enough snippet text here for testing."},
        ]
        with patch.dict(os.environ, {"RESEARCH_MAX_QUERIES": "1"}):
            with self._patch_ddgs(dupes):
                result = provider.research("test topic")
        urls = [s.url for s in result.sources]
        assert len(urls) == len(set(urls)), "Duplicate URLs found in sources"

    # ── Invalid / missing source fields ──────────────────────────────────

    def test_results_missing_title_are_skipped(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        results = [
            {"title": "", "href": "https://example.com/a",
             "body": "Long enough snippet for testing purposes here."},
            {"title": "Valid Title", "href": "https://example.com/b",
             "body": "Another long enough snippet for testing purposes."},
        ]
        with patch.dict(os.environ, {"RESEARCH_MAX_QUERIES": "1"}):
            with self._patch_ddgs(results):
                result = provider.research("test topic")
        assert all(s.title for s in result.sources)

    def test_results_with_invalid_url_are_skipped(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        results = [
            {"title": "Bad URL source", "href": "not-a-real-url",
             "body": "Long enough snippet for testing purposes here."},
            {"title": "Good source", "href": "https://example.com/good",
             "body": "A proper long snippet text for testing purposes here."},
        ]
        with patch.dict(os.environ, {"RESEARCH_MAX_QUERIES": "1"}):
            with self._patch_ddgs(results):
                result = provider.research("test topic")
        for src in result.sources:
            assert src.url.startswith("http")

    def test_results_with_short_snippet_are_skipped(self):
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        results = [
            {"title": "Too short", "href": "https://example.com/short",
             "body": "tiny"},   # too short
            {"title": "Good source", "href": "https://example.com/good",
             "body": "A proper long snippet text for testing purposes here."},
        ]
        with patch.dict(os.environ, {"RESEARCH_MAX_QUERIES": "1"}):
            with self._patch_ddgs(results):
                result = provider.research("test topic")
        assert all(len(s.snippet) >= 20 for s in result.sources)

    # ── Query generation ──────────────────────────────────────────────────

    def test_multiple_queries_generated_for_why_topic(self):
        from backend.services.research.web_provider import _build_queries
        queries = _build_queries("Why do cats purr?", max_queries=3)
        assert len(queries) == 3
        assert queries[0] == "Why do cats purr?"

    def test_single_query_for_quick_depth(self):
        """depth='quick' should pass max_queries=1 to _build_queries."""
        from backend.services.research.web_provider import WebResearchProvider
        provider = WebResearchProvider()
        with patch("backend.services.research.web_provider._build_queries",
                   wraps=lambda topic, max_queries: [topic]) as mock_bq:
            with self._patch_ddgs(self._make_ddg_results(3)):
                provider.research("Why do cats purr?", depth="quick")
        mock_bq.assert_called_once_with("Why do cats purr?", 1)

    def test_url_validation(self):
        from backend.services.research.web_provider import _is_valid_url
        assert _is_valid_url("https://example.com") is True
        assert _is_valid_url("http://example.com/path") is True
        assert _is_valid_url("not-a-url") is False
        assert _is_valid_url("ftp://example.com") is False
        assert _is_valid_url("") is False

    def test_provider_name(self):
        from backend.services.research.web_provider import WebResearchProvider
        assert WebResearchProvider().provider_name == "web"


# ── Script generation blocked when no sources ─────────────────────────────────

class TestScriptGenerationRequiresResearch:
    """Ensure script generation is blocked when research returns no sources."""

    def test_no_sources_raises_no_sources_error(self):
        """generate_script must raise ResearchNoSourcesError when sources=[].

        This test patches the research provider to return a result with zero
        sources (simulating a topic that returns nothing from web search).
        """
        from backend.services.research.base import ResearchNoSourcesError
        from backend.services.research.base import ResearchResult
        from backend.services.script_generator import generate_script
        from backend.content_models import ScriptRequest

        empty_research = ResearchResult(
            topic="xyzzy",
            sources=[],
            key_facts=[],
            research_summary="No results.",
        )

        with patch("backend.services.script_generator.get_research_provider") as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.return_value = empty_research
            mock_factory.return_value = mock_provider

            with pytest.raises(ResearchNoSourcesError):
                generate_script(ScriptRequest(
                    topic="xyzzy",
                    language="en",
                    tone="informative",
                    target_duration_seconds=180,
                    scene_count=5,
                ))

    def test_api_returns_422_not_500_when_no_sources(self, client):
        """API must return 422 (not 500) when research finds no sources."""
        from backend.services.research.base import ResearchNoSourcesError

        with patch(
            "backend.routers.content.generate_script",
            side_effect=ResearchNoSourcesError(
                "No reliable web sources were found for: 'xyzzy_unknown_topic'."
            ),
        ):
            r = client.post(
                "/api/content/generate-script",
                json={
                    "topic": "xyzzy_unknown_topic",
                    "language": "en",
                    "tone": "informative",
                    "target_duration_seconds": 180,
                    "scene_count": 5,
                },
            )

        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "no reliable web sources" in detail.lower()

    def test_research_endpoint_returns_422_not_500_when_no_sources(self, client):
        """POST /api/content/research must return 422 when search finds nothing."""
        from backend.services.research.base import ResearchNoSourcesError

        with patch(
            "backend.routers.content.get_research_provider"
        ) as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.side_effect = ResearchNoSourcesError(
                "No reliable web sources were found for: 'unknowntopic'."
            )
            mock_factory.return_value = mock_provider

            r = client.post(
                "/api/content/research",
                json={"topic": "unknowntopic", "language": "en", "depth": "standard"},
            )

        assert r.status_code == 422
        assert "no reliable web sources" in r.json()["detail"].lower()

    def test_rate_limit_returns_429(self, client):
        from backend.services.research.base import ResearchRateLimitError

        with patch(
            "backend.routers.content.get_research_provider"
        ) as mock_factory:
            mock_provider = MagicMock()
            mock_provider.research.side_effect = ResearchRateLimitError(
                "DuckDuckGo rate-limited the request."
            )
            mock_factory.return_value = mock_provider

            r = client.post(
                "/api/content/research",
                json={"topic": "cats", "language": "en", "depth": "standard"},
            )

        assert r.status_code == 429


# ── Research factory tests ────────────────────────────────────────────────────

class TestResearchFactory:
    def test_web_provider_selected_by_default(self):
        from backend.services.research.factory import get_research_provider
        from backend.services.research.web_provider import WebResearchProvider
        with patch.dict(os.environ, {"RESEARCH_PROVIDER": "web"}):
            provider = get_research_provider()
        assert isinstance(provider, WebResearchProvider)

    def test_unknown_provider_raises_error(self):
        from backend.services.research.base import ResearchError
        from backend.services.research.factory import get_research_provider
        with patch.dict(os.environ, {"RESEARCH_PROVIDER": "unknown"}):
            with pytest.raises(ResearchError):
                get_research_provider()
