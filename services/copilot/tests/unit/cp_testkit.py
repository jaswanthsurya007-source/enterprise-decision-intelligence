"""Importable unit-test helpers for the copilot suite (no infra/keys).

These live in a uniquely-named module (not ``conftest``) so they can be
imported by test modules under pytest's ``importlib`` import mode without
colliding with another service's ``tests.conftest`` plugin name in a
whole-repo run. The tests dir is placed on ``pythonpath`` (see the root
``pyproject.toml``) so ``from cp_testkit import ...`` resolves.

Contains the scripted ``FakeLLM`` AsyncAnthropic stand-in and the tenant
constants the unit tests assert against.
"""

from __future__ import annotations

TENANT = "acme"
OTHER_TENANT = "globex"


# ---------------------------------------------------------------------------
# FakeLLM — a scripted AsyncAnthropic stand-in (no network, no key).
# ---------------------------------------------------------------------------
# Minimal duck-typed SDK message/block shapes the manual loop reads.
class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    def __init__(self, **kw):
        # Default a warm cache read so tests can assert cache_read_input_tokens > 0.
        self.input_tokens = kw.get("input_tokens", 1200)
        self.output_tokens = kw.get("output_tokens", 200)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 5000)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)


class _Msg:
    def __init__(self, *, stop_reason, content, usage=None, stop_details=None):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage or _Usage()
        self.stop_details = stop_details


def text_block(text):
    return _Block(type="text", text=text)


def tool_use_block(name, tool_input, *, id="toolu_1"):
    return _Block(type="tool_use", name=name, input=dict(tool_input), id=id)


def assistant_turn(*, stop_reason, content, **kw):
    """Build a scripted assistant message the FakeLLM returns for the next call."""

    return _Msg(stop_reason=stop_reason, content=list(content), **kw)


class _FakeStream:
    def __init__(self, msg, deltas):
        self._msg = msg
        self._deltas = deltas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            for d in self._deltas:
                yield _Block(
                    type="content_block_delta",
                    delta=_Block(type="text_delta", text=d),
                )

        return _gen()

    async def get_final_message(self):
        return self._msg


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def stream(self, **kwargs):
        self._owner.stream_calls.append(kwargs)
        msg = self._owner._next_message()
        # Stream the text-block deltas so the loop emits token frames.
        deltas = [b.text for b in (msg.content or []) if getattr(b, "type", None) == "text"]
        return _FakeStream(msg, deltas)

    async def count_tokens(self, **kwargs):
        self._owner.count_calls.append(kwargs)
        return _Block(input_tokens=1234)

    async def parse(self, **kwargs):
        self._owner.parse_calls.append(kwargs)
        return _Block(parsed_output=self._owner.route_output)


class FakeLLM:
    """A scripted ``AsyncAnthropic`` stand-in: a queue of messages the loop consumes.

    Construct with the ordered ``messages`` the loop should receive across its
    iterations (tool_use turns then a final end_turn turn). ``route_output`` is the
    object ``messages.parse`` returns for routing (defaults to an rca/last_week route).
    Records ``stream_calls`` / ``count_calls`` / ``parse_calls`` for assertions.
    """

    def __init__(self, messages, *, route_output=None):
        self._messages = list(messages)
        self.messages = _FakeMessages(self)
        self.stream_calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.parse_calls: list[dict] = []
        self.route_output = route_output or _Block(
            intent="rca", time_range="last_week", scope_ok=True
        )

    def _next_message(self):
        if self._messages:
            return self._messages.pop(0)
        # Default terminal turn if the script runs dry.
        return _Msg(stop_reason="end_turn", content=[text_block("done")])
