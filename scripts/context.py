"""Path resolution for questgen v1. All scripts go through Ctx; nothing hard-codes subjects."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent


def _load_config() -> dict:
    cfg_path = ENGINE_ROOT / "config" / "config.yaml"
    try:
        import yaml  # type: ignore
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


CONFIG = _load_config()
_DEF = CONFIG.get("defaults", {})


def reload_config() -> dict:
    """Re-read config.yaml into the module-global CONFIG in place (so existing
    `context.CONFIG` references see the update). Used by the dashboard config panel."""
    fresh = _load_config()
    CONFIG.clear()
    CONFIG.update(fresh)
    return CONFIG


def config_path() -> Path:
    return ENGINE_ROOT / "config" / "config.yaml"


def content_root(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("QUESTGEN_CONTENT")
    if env:
        return Path(env).expanduser().resolve()
    rel = CONFIG.get("content_root", "../content")
    return (ENGINE_ROOT / rel).resolve()


@dataclass
class Ctx:
    subject: str = _DEF.get("subject", "math")
    stage: str = _DEF.get("stage", "primary")
    level: str = _DEF.get("level", "p6")
    source: str = _DEF.get("source", "")
    root: Path = None  # type: ignore

    def __post_init__(self):
        if self.root is None:
            self.root = content_root()

    @property
    def stage_dir(self) -> Path:
        return self.root / self.subject / self.stage

    @property
    def level_dir(self) -> Path:
        return self.root / self.subject / self.stage / self.level

    @property
    def source_dir(self) -> Path:
        return self.level_dir / self.source

    @property
    def original_dir(self) -> Path:
        return self.source_dir / "original"

    @property
    def raw_dir(self) -> Path:
        return self.source_dir / "raw"

    @property
    def extracted_dir(self) -> Path:
        return self.source_dir / "extracted"

    @property
    def interim_dir(self) -> Path:
        return self.source_dir / "interim"

    @property
    def ops_path(self) -> Path:
        return self.raw_dir / "ops.json"

    @property
    def outputs_dir(self) -> Path:
        """Workspace-level outputs (worksheets etc.): <ws>/outputs, sibling of content/."""
        return self.root.parent / "outputs"


def add_ctx_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--subject", default=_DEF.get("subject", "math"))
    p.add_argument("--stage", default=_DEF.get("stage", "primary"))
    p.add_argument("--level", default=_DEF.get("level", "p6"))
    p.add_argument("--source", default=_DEF.get("source", ""))
    p.add_argument("--content-root", default=None)


def ctx_from_args(a: argparse.Namespace) -> Ctx:
    return Ctx(a.subject, a.stage, a.level, a.source, content_root(a.content_root))


PIPELINE_DIRS = ("original", "raw", "extracted", "interim")   # markers of a real source folder


def discover(root: Path) -> list[dict]:
    """Walk content tree -> [{subject, stage, level, source}]. Layout is
    <subject>/<stage>/<level>/<source>/{original,raw,extracted,interim}; metadata dirs
    (_taxonomy, _alignment, db, ...) are recognised by NOT containing a pipeline subdir,
    so no naming convention is required to exclude them."""
    out = []
    if not root.is_dir():
        return out

    def kids(p):
        return sorted(c for c in p.iterdir() if c.is_dir() and not c.name.startswith((".", "_")))

    for subj in kids(root):
        for stage in kids(subj):
            for level in kids(stage):
                for src in kids(level):
                    if any((src / d).is_dir() for d in PIPELINE_DIRS):   # a source, not stray metadata
                        out.append({"subject": subj.name, "stage": stage.name,
                                    "level": level.name, "source": src.name})
    return out
