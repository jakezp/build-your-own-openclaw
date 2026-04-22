"""Configuration management with hot reload support."""

import logging
import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


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


class TelegramConfig(BaseModel):
    """Telegram platform configuration."""

    enabled: bool = True
    bot_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)


class DiscordConfig(BaseModel):
    """Discord platform configuration."""

    enabled: bool = True
    bot_token: str
    channel_id: str | None = None
    allowed_user_ids: list[str] = Field(default_factory=list)


class BraveWebSearchConfig(BaseModel):
    """Configuration for web search provider."""

    provider: Literal["brave"] = "brave"
    api_key: str


class Crawl4AIWebReadConfig(BaseModel):
    """Configuration for web read provider."""

    provider: Literal["crawl4ai"] = "crawl4ai"


class SourceSessionConfig(BaseModel):
    """Session affinity configuration for a source."""

    session_id: str


class ChannelConfig(BaseModel):
    """Channel configuration."""

    enabled: bool = False
    telegram: TelegramConfig | None = None
    discord: DiscordConfig | None = None


class Config(BaseModel):
    """Main configuration with hot reload support."""

    workspace: Path
    llm: LLMConfig
    default_agent: str
    agents_path: Path = Field(default=Path("agents"))
    skills_path: Path = Field(default=Path("skills"))
    logging_path: Path = Field(default=Path(".logs"))
    history_path: Path = Field(default=Path(".history"))
    event_path: Path = Field(default=Path(".event"))
    websearch: BraveWebSearchConfig | None = None
    webread: Crawl4AIWebReadConfig | None = None
    channels: ChannelConfig = Field(default_factory=ChannelConfig)
    sources: dict[str, SourceSessionConfig] = Field(default_factory=dict)
    default_delivery_source: str | None = None

    @model_validator(mode="after")
    def resolve_paths(self) -> "Config":
        """Resolve relative paths to absolute using workspace."""
        for field_name in (
            "agents_path",
            "skills_path",
            "logging_path",
            "history_path",
            "event_path",
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
        config_data = cls._load_merged_configs(workspace_dir)
        config_data["workspace"] = workspace_dir
        return cls.model_validate(config_data)

    @classmethod
    def _load_merged_configs(cls, workspace_dir: Path) -> dict[str, Any]:
        """Load and merge user and runtime config files."""
        config_data: dict[str, Any] = {}

        user_config = workspace_dir / "config.user.yaml"
        runtime_config = workspace_dir / "config.runtime.yaml"

        if user_config.exists():
            with open(user_config) as f:
                config_data = cls._deep_merge(config_data, yaml.safe_load(f) or {})

        if runtime_config.exists():
            with open(runtime_config) as f:
                config_data = cls._deep_merge(config_data, yaml.safe_load(f) or {})

        return config_data

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep merge override dict into base dict."""
        result = base.copy()

        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = Config._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def _set_nested(self, obj: dict, key: str, value: Any) -> None:
        """Set a nested value in a dict using dot notation."""
        keys = key.split(".")
        for k in keys[:-1]:
            if k not in obj or not isinstance(obj[k], dict):
                obj[k] = {}
            obj = obj[k]
        obj[keys[-1]] = value

    def _set_config_value(self, config_path: Path, key: str, value: Any) -> None:
        """Update a config value in a YAML file."""
        # Load existing or start fresh
        if config_path.exists():
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        if isinstance(value, BaseModel):
            value = value.model_dump()

        # Update the key (supports nested via dot notation)
        self._set_nested(data, key, value)

        # Write back
        with open(config_path, "w") as f:
            yaml.dump(data, f)

    def set_user(self, key: str, value: Any) -> None:
        """Update a config value in config.user.yaml."""
        self._set_config_value(self.workspace / "config.user.yaml", key, value)

    def set_runtime(self, key: str, value: Any) -> None:
        """Update a runtime value in config.runtime.yaml."""
        self._set_config_value(self.workspace / "config.runtime.yaml", key, value)

    def reload(self) -> bool:
        """Re-read config.user.yaml and merge with runtime."""
        try:
            config_data = self._load_merged_configs(self.workspace)
            config_data["workspace"] = self.workspace

            # Create new instance and copy values
            new_config = Config.model_validate(config_data)

            # Update all fields from new config
            for field_name in Config.model_fields:
                setattr(self, field_name, getattr(new_config, field_name))

            return True
        except Exception as e:
            logging.debug("Config reload failed: %s", e)
            return False


class ConfigHandler(FileSystemEventHandler):
    """Handles config file modification events."""

    def __init__(self, config: Config):
        self._config = config

    def on_modified(self, event):
        """Reload config when config.user.yaml changes."""
        if not event.is_directory and event.src_path.endswith("config.user.yaml"):
            self._config.reload()


class ConfigReloader:
    """Manages watchdog observer for config hot reload."""

    def __init__(self, config: Config):
        self._config = config
        self._observer = Observer()

    def start(self) -> None:
        """Start watching config file for changes."""
        handler = ConfigHandler(self._config)
        self._observer.schedule(handler, str(self._config.workspace), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        """Stop watching."""
        self._observer.stop()
        self._observer.join()
        del self._observer
