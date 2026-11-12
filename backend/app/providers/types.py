"""Internal execution DTOs: never credentials or verified citations."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=131072)


@dataclass(frozen=True)
class TextDelta:
    text: str
    type: Literal["delta"] = "delta"


@dataclass(frozen=True)
class StreamDone:
    model: str
    finish_reason: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    type: Literal["done"] = "done"


@dataclass(frozen=True)
class EmbeddingResult:
    model: str
    vectors: tuple[tuple[float, ...], ...]

    @property
    def dimensions(self) -> int:
        return len(self.vectors[0])


@dataclass(frozen=True)
class Limits:
    input_bytes: int = 131072
    output_bytes: int = 262144
    frame_bytes: int = 65536
    response_bytes: int = 8388608
    max_output_tokens: int = 8192
    batch_size: int = 64
    dimensions: int = 8192
    deadline: float = 120
    read_timeout: float = 20


StreamEvent = TextDelta | StreamDone
