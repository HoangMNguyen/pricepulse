"""Programmatic `alembic upgrade head`, shared by the CLI and the migrate Lambda."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config() -> Config:
    """Locate alembic.ini by walking up from this file: works for the `src/` layout locally
    and for the flat Lambda zip where the package sits next to alembic.ini."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "alembic.ini"
        if candidate.is_file():
            return Config(str(candidate))
    raise FileNotFoundError("alembic.ini not found above " + __file__)


def upgrade_head() -> None:
    command.upgrade(alembic_config(), "head")
