"""
backend/services/video/templates.py — Phase 3E.1 Template Foundation.

Template registry and configuration for video generation styles.
CPU-friendly, no GPU required, no paid APIs.

Templates:
  - minimal_dark: Preserve current visual behavior (gradient + text)
  - quote_fact: Large quote/fact emphasis for impactful content
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TemplateConfig:
    """Configuration for a video template."""
    template_id: str
    name: str
    description: str
    background_type: str  # "gradient", "solid"
    layout_type: str     # "centered", "quote_emphasis"
    typography_style: str # "minimal", "bold", "classic"
    caption_style: str   # "bottom", "centered"
    supports_animation: bool


# ── Template Registry ────────────────────────────────────────────────────────

TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "minimal_dark": TemplateConfig(
        template_id="minimal_dark",
        name="Minimal Dark",
        description="Clean gradient background with centered text (existing style)",
        background_type="gradient",
        layout_type="centered",
        typography_style="minimal",
        caption_style="bottom",
        supports_animation=False,
    ),
    "quote_fact": TemplateConfig(
        template_id="quote_fact",
        name="Quote/Fact",
        description="Large quote emphasis with bold typography for impactful content",
        background_type="gradient",
        layout_type="quote_emphasis",
        typography_style="bold",
        caption_style="centered",
        supports_animation=False,
    ),
}


# ── Template Access Functions ───────────────────────────────────────────────

def get_template(template_id: str) -> TemplateConfig:
    """
    Get template configuration by ID.
    Falls back to minimal_dark if template not found.
    """
    template = TEMPLATE_REGISTRY.get(template_id)
    if template is None:
        # Fallback to minimal_dark for invalid template IDs
        return TEMPLATE_REGISTRY["minimal_dark"]
    return template


def list_templates() -> list[TemplateConfig]:
    """List all available templates."""
    return list(TEMPLATE_REGISTRY.values())


def is_valid_template(template_id: str) -> bool:
    """Check if template ID is valid."""
    return template_id in TEMPLATE_REGISTRY


def get_default_template() -> TemplateConfig:
    """Get the default template (minimal_dark)."""
    return TEMPLATE_REGISTRY["minimal_dark"]
