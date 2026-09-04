"""Plain data shapes passed between stages.

Every stage is a pure function over these except the edges (fetch, LLM, files).
Each has to_dict/from_dict so a stage can be run from a JSON fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    section: str
    weight: float = 1.0


@dataclass
class Item:
    """One feed entry, before any judgement is made about it."""

    id: str  # sha1 of the canonical url
    source: str
    section: str
    title: str
    blurb: str
    url: str
    published: datetime
    weight: float = 1.0
    also_in: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        d = dict(d)
        d["published"] = _parse_dt(d["published"])
        d.setdefault("weight", 1.0)
        d.setdefault("also_in", [])
        return cls(**d)


@dataclass
class Evidence:
    """Text about a story beyond what its own feed entry carried.

    `kind` says where it came from and how far to trust it. "article" is the
    story's own page. "search" is another outlet writing about the same event,
    which grounds a thin item but is not the primary source and is never
    presented as one.
    """

    kind: str  # article | search
    text: str
    url: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(kind=d["kind"], text=d.get("text", ""),
                   url=d.get("url", ""), source=d.get("source", ""))


@dataclass
class Classified:
    """An Item plus the classifier's verdict on it."""

    item: Item
    fit: int
    kind: str  # core | adjacent | neither — fixed slots; the lens names them
    novelty: int
    region: str
    domain: str
    mechanism: str | None
    reason: str
    # Gathered after selection, for the items actually being written up. Empty
    # when the feed entry already said enough.
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def rank(self) -> float:
        """Ordering key for the hard cap: fit weighted by source trust."""
        return self.fit * self.item.weight

    def to_dict(self) -> dict:
        return {
            "item": self.item.to_dict(),
            "fit": self.fit,
            "kind": self.kind,
            "novelty": self.novelty,
            "region": self.region,
            "domain": self.domain,
            "mechanism": self.mechanism,
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Classified":
        return cls(
            item=Item.from_dict(d["item"]),
            fit=int(d["fit"]),
            kind=d["kind"],
            novelty=int(d["novelty"]),
            region=d.get("region", "global"),
            domain=d.get("domain", "other"),
            mechanism=d.get("mechanism"),
            reason=d.get("reason", ""),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
        )


@dataclass
class Cluster:
    cluster_id: str
    title: str
    items: list[Classified]
    shared_mechanism: str | None = None

    @property
    def fit(self) -> int:
        return max((c.fit for c in self.items), default=0)

    @property
    def region(self) -> str:
        return self.items[0].region if self.items else "global"

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "title": self.title,
            "shared_mechanism": self.shared_mechanism,
            "items": [c.to_dict() for c in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cluster":
        return cls(
            cluster_id=d["cluster_id"],
            title=d["title"],
            items=[Classified.from_dict(x) for x in d["items"]],
            shared_mechanism=d.get("shared_mechanism"),
        )


@dataclass
class Entry:
    """One written item of the briefing."""

    cluster_id: str
    headline: str
    body: str
    hook: str
    cluster_title: str = ""
    questions: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    fit: int = 0
    region: str = "global"
    mechanism: str | None = None
    item_count: int = 1
    # "source" when a person already wrote this up and we are carrying their
    # words; "written" when the model wrote it. The reader is told which.
    provenance: str = "written"
    attribution: str = ""

    @property
    def word_count(self) -> int:
        text = " ".join([self.headline, self.body, self.hook, *self.questions])
        return len(text.split())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        d = dict(d)
        d.setdefault("provenance", "written")
        d.setdefault("attribution", "")
        return cls(**d)


@dataclass
class Edition:
    """Everything an emitter needs. The one in-memory artifact every format derives from."""

    week: str  # ISO week, e.g. 2026-W36
    generated_at: datetime
    opening: str
    entries: list[Entry]
    closing_questions: list[str]
    theme: str | None = None
    partial: bool = False
    quiet: bool = False
    # What this briefing calls itself, out loud and in the heading. A default
    # rather than a required field so every existing caller and stored edition
    # keeps working.
    title: str = "The weekly digest"

    @property
    def word_count(self) -> int:
        n = len(self.opening.split()) + sum(e.word_count for e in self.entries)
        return n + sum(len(q.split()) for q in self.closing_questions)

    def to_dict(self) -> dict:
        return {
            "week": self.week,
            "title": self.title,
            "generated_at": self.generated_at.isoformat(),
            "opening": self.opening,
            "entries": [e.to_dict() for e in self.entries],
            "closing_questions": self.closing_questions,
            "theme": self.theme,
            "partial": self.partial,
            "quiet": self.quiet,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Edition":
        return cls(
            week=d["week"],
            generated_at=_parse_dt(d["generated_at"]),
            opening=d["opening"],
            entries=[Entry.from_dict(e) for e in d["entries"]],
            closing_questions=d.get("closing_questions", []),
            theme=d.get("theme"),
            partial=d.get("partial", False),
            quiet=d.get("quiet", False),
            title=d.get("title", "The weekly digest"),
        )


@dataclass
class Dropped:
    """Why an item did not make the digest — the audit trail."""

    id: str
    title: str
    stage: str
    reason: str
