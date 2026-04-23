# Step 10: WebSocket

> Programmatic access to the agent via WebSocket.

## Prerequisites

- Steps 00–09 done.

```bash
cd 10-websocket
uv sync
```

## Note on ChatGPT OAuth concurrency

The WebSocket worker serves many clients concurrently from one process.
They all share a single `ChatGPTOAuth` instance, which uses an
in-process `asyncio.Lock` to serialize token reads and refreshes.
That's sufficient for a single-process deployment. For multi-process
setups, step 16 discusses an OS-level file-lock as future work.

## Why this step exists

Step 09 gave your agent human-facing channels — Telegram, Discord, CLI. All of them expect a person typing.

What if you want to **write code** that talks to the agent? A script that uses it to generate reports. A web app whose frontend is a chat UI. Another agent that wants to consult this one. None of those are human-with-a-phone.

A WebSocket endpoint lets any programmatic client connect, exchange structured events, and get responses in real time. Same agent, same event bus, different transport — and no human-facing UI on top.

## The mental model

A WebSocket is a bidirectional persistent connection between a client and a server. Unlike HTTP (request → response → done), the connection stays open and both sides can send messages at any time.

For our purposes:

- The server (this agent) exposes `ws://host:port/ws`.
- A client connects, gets a session_id (either by passing one or having one assigned).
- The client sends JSON-encoded inbound events; the server publishes them to the bus.
- The server subscribes to outbound events for this session; when one fires, it serializes and ships it down the WebSocket.

The `WebSocketWorker` runs the server, manages connections, and plumbs events in both directions.

## Key decisions

### Decision 1: WebSocket as just another channel-shaped worker

The `WebSocketWorker` is structurally similar to a channel: it's a long-running listener that publishes `InboundEvent`s and subscribes to `OutboundEvent`s. The differences from Telegram/Discord:

- No bot token, no third-party API.
- The "source" is a live socket, not a chat ID. The DeliveryWorker needs to track open sockets per session.
- Multiple clients can connect to the same session (useful for multi-screen, multi-device).

Inheriting from `SubscriberWorker` keeps the lifecycle shape consistent with every other worker.

### Decision 2: JSON wire format

Events on the wire are JSON:

```json
{"type": "InboundEvent", "session_id": "...", "content": "hello"}
```

The `serialize_event`/`deserialize_event` helpers from step 07 already do this. Clients need to speak the same JSON shape — which is documented by the event dataclasses.

A protobuf or MessagePack format would be faster but harder to debug. JSON is good enough for a tutorial.

### Decision 3: one worker, many connections

`WebSocketWorker` holds a `dict[session_id, set[WebSocketConnection]]`. Many clients can connect, but each message goes to the right subset:

- Inbound: routed by the `session_id` in the incoming message.
- Outbound: broadcast to every connection subscribed to that `session_id`.

The "many connections, one session" pattern makes fan-out easy — a web UI and a CLI tool can both watch the same conversation.

### Decision 4: connection lifecycle = subscription lifecycle

When a client connects, it's added to the session's connection set. When it disconnects (normal close, network error, timeout), it's removed. If the set becomes empty for a session, no events fire for that session until a new client reconnects — the bus still publishes them, the worker just has no socket to write to.

This is fire-and-forget by design. Clients that care about never missing a message use the persistence machinery from step 09.

### Decision 5: FastAPI + `uvicorn` for the HTTP side

Rolling a raw WebSocket server in `asyncio` is possible but noisy (handshake, framing, ping/pong). FastAPI gives us a WebSocket endpoint decorator and handles all that. We're using it just for transport, not for the rest of FastAPI's feature set.

## Read the code

### 1. `src/mybot/server/websocket_worker.py`

```python
class WebSocketWorker(SubscriberWorker):
    def __init__(self, context):
        super().__init__(context)
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.context.eventbus.subscribe(OutboundEvent, self.broadcast)

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        self.connections[session_id].add(websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                event = deserialize_event(data)
                await self.context.eventbus.publish(event)
        except WebSocketDisconnect:
            self.connections[session_id].discard(websocket)

    async def broadcast(self, event: OutboundEvent) -> None:
        for conn in self.connections.get(event.session_id, set()):
            try:
                await conn.send_text(json.dumps(event.to_dict()))
            except Exception:
                self.connections[event.session_id].discard(conn)
```

The connection handler reads raw text, deserializes to a typed event, publishes. The broadcast handler walks open connections for the session and writes the outbound event.

### 2. `src/mybot/server/app.py`

FastAPI glue:

```python
app = FastAPI()

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await ws_worker.connect(websocket, session_id)
```

One endpoint, one handler. FastAPI does the rest.

### 3. `src/mybot/server/server.py`

The server entrypoint. Builds `SharedContext`, starts the `EventBus`, starts the `AgentWorker`, `ChannelWorker`s, `DeliveryWorker`, and the `WebSocketWorker`. Serves via `uvicorn` on a configured port.

## Try it out

Start the server:

```bash
uv run my-bot serve    # new command; see cli/main.py
```

In another terminal, use `wscat` or any WebSocket client:

```bash
wscat -c ws://localhost:8000/ws/my-test-session
> {"type":"InboundEvent","session_id":"my-test-session","content":"hello, are you there?"}
< {"type":"OutboundEvent","session_id":"my-test-session","content":"Yes, I'm here. What can I do for you?"}
```

Or from Python:

```python
import asyncio
import json
import websockets

async def main():
    uri = "ws://localhost:8000/ws/my-test-session"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type": "InboundEvent",
            "session_id": "my-test-session",
            "content": "what's 2+2?"
        }))
        reply = json.loads(await ws.recv())
        print(reply["content"])

asyncio.run(main())
```

## Exercises

1. **Two connections, same session.** Open `wscat` twice with the same session_id. Send a message from one. Watch both terminals receive the outbound event. Useful for a web UI plus a debug CLI.

2. **Send an invalid event.** Send `{"type":"NotAnEvent","x":1}`. The deserializer raises. The server logs. The connection stays open. (Does it? Check and fix if not — gracefulness matters.)

3. **Replace the JSON with a typed client.** Generate a JSON Schema for the event dataclasses (pydantic can do this). Ship the schema to your client. Catch mismatch errors at the edge rather than in the server log.

4. **Load test.** Spin up 100 WebSocket clients (`asyncio.gather` in a loop), each sending one message. Watch resource usage. At what concurrency does the event bus become the bottleneck? (Hint: it's a single asyncio queue.)

## What breaks next

You now have a fully event-driven agent reachable from CLI, Telegram, Discord, and WebSocket. Every interaction goes through the same bus, processed by the same `AgentWorker`.

What if you want **multiple specialized agents**, each with different capabilities? A "research" agent with web tools, a "writer" agent with no tools, a "coder" agent with filesystem tools. You want inbound messages to be routed to the right agent automatically.

Step 11 adds a **routing layer** that inspects each inbound event and dispatches to the right agent.

## What's Next

[Step 11: Multi-Agent Routing](../11-multi-agent-routing/) — the right job to the right agent.
