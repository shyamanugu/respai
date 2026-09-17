"""Maps a pipeline name to a constructed `Pipeline` instance — the generic
mechanism the HTTP layer dispatches through. Building the actual usecase
pipelines (which Steps, which prompts, which tools) is not this component's
job; that's what a real usecase supplies via its own entrypoint module (see
`main.py` for the reference shape). See
docs/decisions/0013-serving-hosting-scope.md.
"""
from orchestration.pipeline import Pipeline


class PipelineRegistry:
    def __init__(self) -> None:
        self._pipelines: dict[str, Pipeline] = {}

    def register(self, pipeline: Pipeline) -> None:
        self._pipelines[pipeline.name] = pipeline

    def get(self, name: str) -> Pipeline:
        try:
            return self._pipelines[name]
        except KeyError as exc:
            raise KeyError(f"No pipeline registered under '{name}'") from exc
