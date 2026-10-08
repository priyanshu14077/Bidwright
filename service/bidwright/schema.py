"""The proposal request schema, loaded from data. Field definitions drive the
extraction prompt, the model's output schema, the engine and the review form."""

import json
from dataclasses import dataclass, field
from functools import cache

from bidwright.config import REPO_ROOT

SCHEMA_VERSION = "request-v0"
LIST_KINDS = {"vocab_list", "text_list"}


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    label: str
    group: str
    hint: str = ""
    vocab: str | None = None
    options: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_list(self) -> bool:
        return self.kind in LIST_KINDS


@cache
def fields(version: str = SCHEMA_VERSION) -> dict[str, Field]:
    raw = json.loads((REPO_ROOT / "data" / "schema" / f"{version}.json").read_text())
    return {f["name"]: Field(name=f["name"], kind=f["kind"], label=f["label"], group=f["group"],
                             hint=f.get("hint", ""), vocab=f.get("vocab"), options=tuple(f.get("options", [])))
            for f in raw["fields"]}
