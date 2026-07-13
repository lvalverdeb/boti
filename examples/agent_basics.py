"""
Agent base class — a small LLM-style agent built purely on LifecycleCore.

Agent is what the LifecycleCore extraction was for: an AI agent needs the
sync+async close barrier, context-manager protocol, and leak-warning
finalizer that ManagedResource has always provided, but none of its fsspec
lazy-init or pickle-security gating. This example builds a toy "LLM agent"
around a simulated model session and tool calls to show that Agent holds up
under realistic-shaped use: concurrent tool calls, multi-agent swarms,
sync-only cleanup, and configurable logging.

Demonstrates:
  - AgentConfig: skip_logger, verbose/debug logger levels
  - Async tool-calling under the close barrier (_assert_open() guards use
    after close)
  - A real async _acleanup() override closing a simulated model session
  - A concurrent multi-agent swarm, closed together via asyncio.gather
  - A sync-only agent relying on aclose()'s to-thread _cleanup() fallback
    (the exact scenario a past regression in this codebase broke — see
    boti/tests/core/test_agent.py and the "leaf classes only" warning on
    LifecycleCore._cleanup()/_acleanup())
  - GC leak warning for an agent abandoned without close()
"""

from __future__ import annotations

import asyncio
import gc
import logging
import random
import time
import warnings

from boti.core import Agent


class EchoLLMAgent(Agent):
    """A toy "LLM" agent: a simulated model session plus async tool calls."""

    def __init__(self, name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.name = name
        self.history: list[str] = []
        self.session_open = True

    async def ask(self, prompt: str) -> str:
        self._assert_open()
        await asyncio.sleep(random.uniform(0.01, 0.03))  # simulate model latency
        reply = f"[{self.name}] you said: {prompt!r}"
        self.history.append(reply)
        return reply

    async def use_tool(self, tool: str, **kwargs: object) -> str:
        self._assert_open()
        await asyncio.sleep(random.uniform(0.01, 0.02))  # simulate tool I/O
        result = f"{tool}({kwargs}) -> ok"
        self.history.append(f"[tool] {result}")
        return result

    async def _acleanup(self) -> None:
        await asyncio.sleep(0.01)  # simulate closing the model session
        self.session_open = False
        if self.logger is not None:
            self.logger.info(f"{self.name}: model session closed")


class BlockingToolAgent(Agent):
    """An agent whose only tool is a blocking (sync-only) client.

    Deliberately overrides _cleanup() but NOT _acleanup(): aclose() falls
    back to running _cleanup() in a worker thread automatically, so this
    agent still supports "async with" without writing any async code.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls = 0
        self.flushed = False

    def call_blocking_tool(self) -> None:
        self._assert_open()
        time.sleep(0.01)  # simulate a blocking network call
        self.calls += 1

    def _cleanup(self) -> None:
        self.flushed = True


async def example_single_agent_conversation() -> None:
    """Basic async lifecycle: ask a question, call a tool, close cleanly."""
    async with EchoLLMAgent("assistant", skip_logger=True) as agent:
        print(f"  {await agent.ask('what is the weather in Lima?')}")
        print(f"  {await agent.use_tool('get_weather', city='Lima')}")

    print(f"  closed={agent.closed}, session_open={agent.session_open}")
    print()


async def example_agent_swarm() -> None:
    """A small swarm of agents works concurrently, then closes together.

    aclose()'s close barrier means each agent's cleanup runs exactly once
    even though every agent in the swarm is closed via the same gather().
    """
    agents = [EchoLLMAgent(f"worker-{i}", skip_logger=True) for i in range(5)]
    try:
        replies = await asyncio.gather(
            *(agent.ask(f"task #{i}") for i, agent in enumerate(agents))
        )
        for reply in replies:
            print(f"  {reply}")
    finally:
        await asyncio.gather(*(agent.aclose() for agent in agents))

    print(f"  all {len(agents)} agents closed: {all(agent.closed for agent in agents)}")
    print()


async def example_sync_only_agent_via_aclose_fallback() -> None:
    """A sync-only agent still works under "await aclose()"."""
    agent = BlockingToolAgent(skip_logger=True)
    agent.call_blocking_tool()
    agent.call_blocking_tool()
    await agent.aclose()

    print(f"  closed={agent.closed}, calls={agent.calls}, "
          f"flushed via to_thread fallback={agent.flushed}")
    print()


def example_verbose_and_debug_logging() -> None:
    """AgentConfig's verbose/debug flags control the default logger's level.

    Each case is read immediately after construction and closed before the
    next one starts: Logger.default_logger() caches by class name, so
    reading all three levels only after creating all three agents would
    observe the last set_level() call clobbering the earlier ones.
    """
    for label, overrides in [
        ("default", {}),
        ("verbose", {"verbose": True}),
        ("verbose+debug", {"verbose": True, "debug": True}),
    ]:
        agent = EchoLLMAgent("logger-demo", **overrides)
        try:
            level = logging.getLevelName(agent.logger._core.level)
            print(f"  {label:16s} -> level={level}")
        finally:
            agent.close()
    print()


def example_gc_warns_on_abandoned_agent() -> None:
    """An agent that is garbage collected without close() emits a warning,
    so a forgotten agent in a long-running service is never silent."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _leaked = EchoLLMAgent("forgotten", skip_logger=True)
        _leaked = None
        gc.collect()

        leak_warnings = [w for w in caught if "garbage collected" in str(w.message)]
        if leak_warnings:
            print(f"  leak warning issued: {leak_warnings[0].message}")
        else:
            print("  (no warning — agent was not collected)")
    print()


async def async_main() -> None:
    print("=== Single-agent conversation ===\n")
    await example_single_agent_conversation()
    print("=== Concurrent agent swarm ===\n")
    await example_agent_swarm()
    print("=== Sync-only agent via aclose() fallback ===\n")
    await example_sync_only_agent_via_aclose_fallback()


def main() -> None:
    asyncio.run(async_main())
    print("=== AgentConfig logging levels ===\n")
    example_verbose_and_debug_logging()
    print("=== GC leak warning ===\n")
    example_gc_warns_on_abandoned_agent()


if __name__ == "__main__":
    main()
