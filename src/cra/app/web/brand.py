"""What a deployment may change about how the interface looks and what it says.

A brand is a directory (``CRA_BRAND_DIR``) holding any of the files in
``FALLBACKS``. Each file missing from it is taken from the package's own brand,
so a deployment supplies only what it changes. ``brand.json`` carries content
rather than looks: example questions and the institutions the collaboration
graph tells apart. The pipeline map is the package's own (``pipeline.py``).

The colours and fonts are CSS custom properties in ``static/css/tokens.css``;
``theme.css`` overrides them. The token names are the stable interface, the
rest of ``app.css`` is not.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parent / "brand"
MANIFEST = "brand.json"

# The file a request names, and where to look for it in order: "own" is the
# deployment's brand directory, "default" the package's. A dark logo falls back
# to the deployment's light one before the package's, so a brand that ships a
# single logo never shows another brand's mark in dark mode.
FALLBACKS: dict[str, tuple[tuple[str, str], ...]] = {
    "logo-square-light.svg": (
        ("own", "logo-square-light.svg"),
        ("default", "logo-square-light.svg"),
    ),
    "logo-square-dark.svg": (
        ("own", "logo-square-dark.svg"),
        ("own", "logo-square-light.svg"),
        ("default", "logo-square-dark.svg"),
    ),
    "logo-wide-light.svg": (("own", "logo-wide-light.svg"),),
    "logo-wide-dark.svg": (
        ("own", "logo-wide-dark.svg"),
        ("own", "logo-wide-light.svg"),
    ),
    "favicon.svg": (
        ("own", "favicon.svg"),
        ("own", "logo-square-light.svg"),
        ("default", "favicon.svg"),
    ),
    "theme.css": (("own", "theme.css"), ("default", "theme.css")),
}


class BrandError(ValueError):
    """The brand directory cannot be used as it is."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Institution(_Strict):
    # matched against the institution recorded for each PI in the library
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    name: str = ""


class Manifest(_Strict):
    examples: list[str] = []
    institutions: list[Institution] = []
    # Brands once described the pipeline map; the package does now. An old
    # block is ignored rather than refused, so upgrading needs no brand change.
    pipeline: Any = Field(default=None, exclude=True)


@dataclass(frozen=True)
class Brand:
    manifest: Manifest
    files: dict[str, Path]
    # changes whenever any file the brand serves does, so a URL carrying it
    # never lets a browser keep a previous deployment's logo
    version: str

    @property
    def has_wide_logo(self) -> bool:
        return "logo-wide-light.svg" in self.files

    @classmethod
    def load(cls, own: Path | None) -> "Brand":
        roots = {"default": DEFAULT_DIR, "own": own}
        files: dict[str, Path] = {}
        for name, chain in FALLBACKS.items():
            for root, candidate in chain:
                base = roots[root]
                if base is not None and (base / candidate).is_file():
                    files[name] = base / candidate
                    break
        manifest = _manifest(own / MANIFEST if own else DEFAULT_DIR / MANIFEST)
        if manifest.pipeline is not None:
            log.warning(
                "brand.json: 'pipeline' is no longer read; the package draws its own map"
            )
        digest = hashlib.sha256()
        for name in sorted(files):
            digest.update(name.encode())
            digest.update(files[name].read_bytes())
        digest.update(manifest.model_dump_json().encode())
        return cls(manifest=manifest, files=files, version=digest.hexdigest()[:12])

    def public(self) -> dict[str, Any]:
        """What the pages fetch: everything but the examples, which the
        configuration hands out a few at a time."""
        return self.manifest.model_dump(exclude={"examples"})


def _manifest(path: Path) -> Manifest:
    if not path.is_file():
        return Manifest()
    try:
        return Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as e:
        raise BrandError(f"{path} is not a valid brand manifest: {e}") from e
