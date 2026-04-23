# Step 08: Config Hot Reload

> Edit config while the agent is running. No restart.

## Prerequisites

- Steps 00–07 done.

```bash
cd 08-config-hot-reload
uv sync
```

## Why this step exists

Step 07 gave you an event bus. The agent, the CLI, and any future workers all run inside one long-lived process. That process reads `config.user.yaml` once at startup, holds the parsed `Config` object in `SharedContext`, and never looks at the file again.

If you want to change the default agent, the model, the temperature, the web search provider — any config knob — you kill the process and start it over. Annoying for development. Bad for production (user sessions get dropped mid-conversation).

The fix is a file watcher. When `config.user.yaml` changes, detect it, reparse, swap the in-place `Config` object's fields for the new values. Every worker that holds a reference to the same `Config` instance immediately sees the update on its next read.

This step also splits the config into two files so the agent itself can modify settings without colliding with what the user is editing.

## The mental model

Two files now:

- **`config.user.yaml`** — human-edited. Model choice, default agent, API keys. User is the author.
- **`config.runtime.yaml`** — agent-edited. Runtime state the agent itself writes (e.g., "user's preferred name is Zane"). Agent is the author.

Both are merged at load time, runtime overlaying user. The merged result is a single `Config` object. When `config.user.yaml` changes on disk, a `watchdog` observer fires, we reparse, merge, and mutate the existing `Config` in place.

"Mutate in place" is the important bit. Every worker has a reference to the same `Config` object. If we replaced it with a new object, everyone's references would stale out. Instead:

```python
def reload(self) -> bool:
    new_config = Config.model_validate(config_data)
    for field_name in Config.model_fields:
        setattr(self, field_name, getattr(new_config, field_name))
```

The new config's field values get copied onto the old object. Same object identity, fresh content. Every holder of a `config` reference sees the update.

## Key decisions

### Decision 1: split user-editable and runtime-editable

Without the split, you'd have a race: user opens `config.user.yaml` in an editor while the agent writes to it. The editor's save clobbers the agent's write, or vice versa.

With the split, there's a clear ownership model:
- The user edits `config.user.yaml`, never `config.runtime.yaml`.
- The agent edits `config.runtime.yaml`, never `config.user.yaml`.
- The merged config is what gets used — runtime wins on conflict (it's the fresher value).

Both files are gitignored. The *example* (`config.example.yaml`) is what you check in.

### Decision 2: watchdog, not polling

We use the `watchdog` library for filesystem events. On macOS it's backed by FSEvents, on Linux by inotify, on Windows by ReadDirectoryChangesW. Native events are instant and cheap; polling would fire on every tick whether the file changed or not.

The alternative — polling every N seconds — would work but has two problems: delay (up to N seconds between edit and reload) and overhead (constant file stat calls). Watchdog is "zero cost when idle, instant when changed."

### Decision 3: reload silently fails on bad yaml

```python
def reload(self) -> bool:
    try:
        new_config = Config.model_validate(config_data)
        ...
    except Exception as e:
        logging.debug("Config reload failed: %s", e)
        return False
```

If the user typoes the YAML mid-edit, the reload fails. The old config stays in place. The agent keeps running. No crash, no "you broke your config" splat.

A production system would surface this to the user. The tutorial just logs it — simple is a feature here.

### Decision 4: `set_user()` and `set_runtime()` helpers

Sometimes the agent itself wants to tweak config (e.g., "the user told me their preferred model is gpt-5.4-mini, persist that"). `set_runtime("llm.model", "gpt-5.4-mini")` writes the change to `config.runtime.yaml`, which triggers the watchdog, which triggers reload, which updates the in-memory config. Everyone sees the change on the next read.

Dot notation for nested keys is a small convenience — `"llm.model"` instead of `{"llm": {"model": "..."}}`.

### Decision 5: the watchdog thread talks to `Config`, not the bus

You might expect the reload event to go through the `EventBus`. It doesn't — the watchdog observer runs on a separate thread and calls `config.reload()` directly.

Why? Because `Config` reload is a simple synchronous mutation. Threading it through the bus would require serialization, async dispatch, and a new event type, for no real benefit. The rule of thumb: use the bus for workflow events (message arrived, response ready), not for plumbing events (config changed, process starting).

## Read the code

### 1. `src/mybot/utils/config.py` — the split and merge

The `Config` class is the same shape as before, plus three additions:

**`_load_merged_configs`** walks both YAML files, merges, returns the dict to validate:

```python
@staticmethod
def _load_merged_configs(workspace: Path) -> dict[str, Any]:
    user_path = workspace / "config.user.yaml"
    runtime_path = workspace / "config.runtime.yaml"

    config_data = {}
    if user_path.exists():
        with open(user_path) as f:
            config_data = yaml.safe_load(f) or {}

    if runtime_path.exists():
        with open(runtime_path) as f:
            runtime_data = yaml.safe_load(f) or {}
        # Deep merge: runtime keys override user keys
        config_data = _deep_merge(config_data, runtime_data)

    return config_data
```

**`reload`** re-reads, revalidates, copies fields in place:

```python
def reload(self) -> bool:
    try:
        config_data = self._load_merged_configs(self.workspace)
        config_data["workspace"] = self.workspace
        new_config = Config.model_validate(config_data)
        for field_name in Config.model_fields:
            setattr(self, field_name, getattr(new_config, field_name))
        return True
    except Exception as e:
        logging.debug("Config reload failed: %s", e)
        return False
```

**`set_user` / `set_runtime`** write a single key (with dot notation) to the appropriate file:

```python
def set_runtime(self, key: str, value: Any) -> None:
    self._set_config_value(self.workspace / "config.runtime.yaml", key, value)
```

### 2. `ConfigHandler` — the watchdog glue

```python
class ConfigHandler(FileSystemEventHandler):
    def __init__(self, config: Config):
        self._config = config

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith("config.user.yaml"):
            self._config.reload()
```

Three lines of actual logic. The watchdog library gives us a base class to subclass; we override `on_modified` to filter events (directory changes, other file changes, all ignored) and call `reload()` when the right file is touched.

### 3. `ConfigReloader` — the lifecycle wrapper

```python
class ConfigReloader:
    def __init__(self, config: Config):
        self._config = config
        self._observer = Observer()

    def start(self) -> None:
        handler = ConfigHandler(self._config)
        self._observer.schedule(handler, str(self._config.workspace), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
```

Starts the watchdog observer when the process boots, stops it when the process shuts down. The main entrypoint wires this in alongside the event bus.

## Try it out

Start the agent:

```bash
uv run my-bot chat
```

In a second terminal, edit the config:

```bash
# Change the default agent or model.
$EDITOR ../default_workspace/config.user.yaml
# Save.
```

Send a message in the first terminal. The new agent persona takes effect immediately — no restart.

Try the error path:

```bash
# Introduce invalid YAML.
echo "this is not yaml:::" >> ../default_workspace/config.user.yaml
```

Check the logs — you'll see "Config reload failed." Fix the YAML, save, next reload succeeds. The agent never crashed.

## Exercises

1. **Watch the reloader in action.** Add `print(f"Config reloaded: agent={self._config.default_agent}")` inside `reload()` on success. Edit the config. See the log fire on every save.

2. **Extend hot reload to models.yaml.** Edit `ConfigHandler.on_modified` to trigger reload when `models.yaml` changes too. Notice: the current check-then-reload won't re-run `check_model_allowlist` on the existing `Config` because that's a validator that only runs during `model_validate`. The full reload does re-validate. So it actually works as-is — but think about the flow.

3. **Add a `/reload` slash command.** Force a reload without waiting for a file change. Useful for "I just saved and want to know it took effect." One-liner: `session.agent.config.reload()`.

4. **Measure the reload latency.** Time from file save to `reload()` being called. Usually milliseconds. Test under load (what if the agent is mid-LLM-call)?

## What breaks next

Your agent can now serve multiple users, process events from a bus, and adapt its config on the fly. But it still only has one input source: the CLI. You can't message it from Telegram, Discord, or a web UI.

Step 09 adds **channels**: plug-in input/output adapters for external platforms.

## What's Next

[Step 09: Channels](../09-channels/) — your agent shows up on your phone.
