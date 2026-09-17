"""Ships trace events to Application Insights via opencensus's
`AzureLogHandler` attached to a standard Python logger. Reads the connection
string from APPLICATIONINSIGHTS_CONNECTION_STRING (see .env.local — never
commit a real value to .env).

Not exercised against a live resource by the automated test suite — same
posture as every other real-Azure backend in this platform. `opencensus` is
imported lazily, inside the default logger factory, so a usecase using only
`InMemoryTracer`/`NullTracer` doesn't need it installed. `logger_factory` is
injectable the same way `provider_factory`/`backend_factory` are elsewhere,
so tests can supply a fake logger without the package present.
"""
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .types import PipelineEvent, StepEvent


def _default_logger_factory() -> logging.Logger:
    from opencensus.ext.azure.log_exporter import AzureLogHandler

    logger = logging.getLogger("llmops.observability")
    logger.setLevel(logging.INFO)
    logger.addHandler(
        AzureLogHandler(connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"])
    )
    return logger


@dataclass
class AzureMonitorTracer:
    logger_factory: Callable[[], logging.Logger] = field(default=_default_logger_factory, repr=False)
    _logger: logging.Logger | None = field(default=None, init=False, repr=False)

    def _get_logger(self) -> logging.Logger:
        if self._logger is None:
            self._logger = self.logger_factory()
        return self._logger

    def record_step(self, event: StepEvent) -> None:
        self._get_logger().info(f"step:{event.step_name}", extra={"custom_dimensions": asdict(event)})

    def record_pipeline(self, event: PipelineEvent) -> None:
        self._get_logger().info(
            f"pipeline:{event.pipeline_name}", extra={"custom_dimensions": asdict(event)}
        )
