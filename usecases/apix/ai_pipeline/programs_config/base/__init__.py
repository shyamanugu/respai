"""Base program infrastructure — shared models, prompt builders, config dataclasses.

Every program folder (e.g. ``telesales/``, ``wcc/``) imports from here.
This keeps the entire program system self-contained under ``programs_config/``.

Exports:
    Config  — PipelineConfig, AzureOpenAIConfig, StorageConfig, ThrottleConfig,
              KPIDefinition, load_program_config, ROOT_PATH
    Models  — Transcript, Utterance, ReflectionResponse, etc.
    Prompts — build_analysis_prompt, build_reflection_prompt, DENOISING_SYSTEM_PROMPT
"""

from ai_pipeline.programs_config.base.config import (
    AzureOpenAIConfig,
    AzureSQLConfig,
    ExpandColumnRule,
    KPIDefinition,
    PipelineConfig,
    ReportSectionDef,
    ROOT_PATH,
    StorageConfig,
    SummaryFieldMapping,
    ThrottleConfig,
    load_mode_config,
    load_program_config,
)
from ai_pipeline.programs_config.base.schemas import (
    ReflectionResponse,
    Transcript,
    TranscriptReference,
    Utterance,
    WeeklyCoachingTip,
)
from ai_pipeline.programs_config.base.prompts import (
    DENOISING_SYSTEM_PROMPT,
    build_analysis_prompt,
    build_reflection_prompt,
)
