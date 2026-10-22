"""Recognition ports; implementations have no storage or desktop ownership."""

from typing import Protocol


class JsonModel(Protocol):
    def complete(self, system: str, data: dict) -> dict: ...


class RecognitionCore(Protocol):
    def recognize(self, message: dict, rule: dict) -> dict: ...
