"""Shared state threaded through a pipeline run."""
import uuid
from dataclasses import dataclass, field


@dataclass
class State:
    """Created once per pipeline run. `session_id` is generated now so every
    step and model call can be linked together later — no telemetry is
    emitted anywhere yet (component 05, Observability, adds that), but
    nothing needs to be re-threaded once it does.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    values: dict = field(default_factory=dict)

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value
