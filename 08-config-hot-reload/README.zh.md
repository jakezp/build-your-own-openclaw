# 步骤 08：配置热重载

> 无需重启即可编辑。

## 前置条件

与步骤 06 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

改配置不用重启服务。用 watchdog 监听文件变化，自动热加载。

<img src="08-config-hot-reload.svg" align="center" width="100%" />


## 关键组件

- **ConfigReloader** - 使用 watchdog 监视工作区中的配置文件更改
- **Config Merging** - 通过深度合并，运行时配置覆盖用户配置


[src/mybot/utils/config.py](src/mybot/utils/config.py)

```python
class Config(BaseModel):
    """Configuration with hot reload support."""

    @classmethod
    def _load_merged_configs(cls, workspace_dir: Path) -> dict[str, Any]:
        config_data: dict[str, Any] = {}

        user_config = workspace_dir / "config.user.yaml"
        runtime_config = workspace_dir / "config.runtime.yaml"

        with open(user_config) as f:
            config_data = cls._deep_merge(config_data, yaml.safe_load(f) or {})

        with open(runtime_config) as f:
            config_data = cls._deep_merge(config_data, yaml.safe_load(f) or {})

        return config_data

    def reload(self) -> bool:
        config_data = self._load_merged_configs(self.workspace)
        new_config = Config.model_validate(config_data)

        for field_name in Config.model_fields:
            setattr(self, field_name, getattr(new_config, field_name))

        return True


class ConfigHandler(FileSystemEventHandler):
    """Handles config file modification events."""

    def __init__(self, config: Config):
        self._config = config

    def on_modified(self, event):
        """Reload config when config.user.yaml changes."""
        if not event.is_directory and event.src_path.endswith("config.user.yaml"):
            self._config.reload()
```

## 试一试

和上一步一样跑，改 `config.user.yaml` 会自动生效。

急的读者跳 [步骤 09：频道](../09-channels/)。

## 下一步

[步骤 09：频道](../09-channels/)  - 支持 CLI、Telegram 和其他接口的多平台支持。
