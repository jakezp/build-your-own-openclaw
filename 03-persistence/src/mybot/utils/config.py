"""Configuration management."""

import fnmatch
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


def _load_models_yaml(workspace: Path) -> tuple[set[str], list[str]]:
    """Return (allowed_ids, glob_patterns) from workspace/models.yaml."""
    path = workspace / "models.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Model allowlist not found at {path}. Copy the one from "
            f"default_workspace/models.yaml into your workspace, or edit it "
            f"to add a newly-released model id."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    allowed = set(data.get("allowed") or [])
    patterns = list(data.get("patterns") or [])
    return allowed, patterns


def _model_is_allowed(model: str, allowed: set[str], patterns: list[str]) -> bool:
    if model in allowed:
        return True
    return any(fnmatch.fnmatchcase(model, p) for p in patterns)


_FORBIDDEN_LLM_FIELDS = frozenset({"api_key", "api_base", "auth"})


class LLMConfig(BaseModel):
    """LLM configuration for the OAuth Edition.

    Only `provider` and `model` are accepted. Credentials live entirely in
    the Token_Store populated by `my-bot login`.
    """

    provider: str
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)

    model_config = {"extra": "allow"}  # we trap forbidden fields below

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_fields(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        present = _FORBIDDEN_LLM_FIELDS.intersection(values.keys())
        if present:
            fields = ", ".join(sorted(present))
            raise ValueError(
                f"llm config contains forbidden field(s): {fields}. "
                f"The OAuth Edition does not accept api_key/api_base/auth. "
                f"Remove them from config.user.yaml and run `my-bot login` "
                f"once to authenticate."
            )
        return values

    @model_validator(mode="after")
    def provider_must_be_openai(self) -> "LLMConfig":
        if self.provider != "openai":
            raise ValueError(
                f"llm.provider must be 'openai' in the OAuth Edition "
                f"(got {self.provider!r}). See README for background."
            )
        return self


class Config(BaseModel):
    """Main configuration for step 03."""

    workspace: Path
    llm: LLMConfig
    default_agent: str
    agents_path: Path = Field(default=Path("agents"))
    skills_path: Path = Field(default=Path("skills"))
    history_path: Path = Field(default=Path(".history"))

    @model_validator(mode="after")
    def resolve_paths(self) -> "Config":
        """Resolve relative paths to absolute using workspace."""
        for field_name in (
            "agents_path",
            "skills_path",
            "history_path",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, self.workspace / path)
        return self

    @model_validator(mode="after")
    def check_model_allowlist(self) -> "Config":
        allowed, patterns = _load_models_yaml(self.workspace)
        if not _model_is_allowed(self.llm.model, allowed, patterns):
            raise ValueError(
                f"llm.model '{self.llm.model}' is not accepted by the ChatGPT "
                f"subscription backend. Currently accepted: "
                f"{sorted(allowed)} plus patterns {patterns}. "
                f"Add the new id to {self.workspace / 'models.yaml'}."
            )
        return self

    @classmethod
    def load(cls, workspace_dir: Path) -> "Config":
        """Load configuration from workspace directory."""
        config_data = cls._load_config(workspace_dir)
        config_data["workspace"] = workspace_dir
        return cls.model_validate(config_data)

    @classmethod
    def _load_config(cls, workspace_dir: Path) -> dict[str, Any]:
        """Load config from YAML file."""
        config_file = workspace_dir / "config.user.yaml"
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")

        with open(config_file) as f:
            return yaml.safe_load(f) or {}
