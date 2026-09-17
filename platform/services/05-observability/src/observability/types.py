"""Event shapes emitted by Orchestration (08) — one `StepEvent` per
`ModelStep.run()`, one `PipelineEvent` per `Pipeline.run()`. Kept as plain
dataclasses (not imported from anywhere) so any `Tracer` implementation can
consume them without a shared dependency beyond this component.
"""
from dataclasses import dataclass


@dataclass
class StepEvent:
    session_id: str
    step_name: str
    model_alias: str | None = None
    provider: str | None = None
    deployment: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    guardrail_allowed: bool = True
    guardrail_reason: str = ""
    error: str | None = None


@dataclass
class PipelineEvent:
    session_id: str
    pipeline_name: str
    step_count: int
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    error: str | None = None
