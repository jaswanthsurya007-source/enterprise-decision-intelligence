"""P3 — prompt-cache effectiveness: a warm ``cache_read_input_tokens`` is surfaced.

The frozen tool schemas + frozen system prefix form a stable, cacheable prompt prefix, so
after warm-up the streamed message reports ``usage.cache_read_input_tokens > 0``. The loop
tracks the max cache-read across iterations and surfaces it on the answer
(``cache_read_input_tokens``), the cache-hit metric the dashboard/telemetry reads. Here the
FakeLLM is scripted so the FIRST call is a cold cache (0 reads) and the SECOND is warm
(>0); the loop must surface the warm read.
"""

from __future__ import annotations

from edis_copilot.agent.loop import answer
from cp_testkit import _Usage, assistant_turn, FakeLLM, text_block, tool_use_block


def _cold_usage() -> _Usage:
    """Usage for the first (cold) call: a cache write, no cache read."""

    return _Usage(
        input_tokens=4200,
        output_tokens=120,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=4000,
    )


def _warm_usage() -> _Usage:
    """Usage for the second (warm) call: the cached prefix is read back."""

    return _Usage(
        input_tokens=300,
        output_tokens=180,
        cache_read_input_tokens=4000,
        cache_creation_input_tokens=0,
    )


async def test_warm_cache_read_surfaced_on_second_call(registry, ctx):
    """First call cold (0 reads), second call warm (>0) -> the loop surfaces the warm read."""

    llm = FakeLLM(
        [
            # Iteration 1 (cold): a tool_use turn, no cache read yet.
            assistant_turn(
                stop_reason="tool_use",
                content=[tool_use_block("find_anomalies", {"metric_key": "revenue"}, id="t1")],
                usage=_cold_usage(),
            ),
            # Iteration 2 (warm): the cached tools+system prefix is read back.
            assistant_turn(
                stop_reason="end_turn",
                content=[text_block("Revenue fell to 61000 from 95000. [1]")],
                usage=_warm_usage(),
            ),
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    assert len(llm.stream_calls) == 2
    # The loop tracks the max cache-read across iterations -> the warm 4000 is surfaced.
    assert result.cache_read_input_tokens >= 4000


async def test_cache_read_zero_when_never_warmed(registry, ctx):
    """A single cold call surfaces a zero cache-read metric (nothing to read back yet)."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="end_turn",
                content=[text_block("A calm summary.")],
                usage=_cold_usage(),
            )
        ]
    )
    result = await answer("status?", ctx, registry=registry, llm=llm)
    assert result.cache_read_input_tokens == 0


async def test_cache_metric_is_max_across_iterations(registry, ctx):
    """The surfaced metric is the MAX cache-read seen, not the last iteration's value."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[tool_use_block("find_anomalies", {"metric_key": "revenue"}, id="t1")],
                usage=_Usage(cache_read_input_tokens=9000),  # high warm read mid-loop
            ),
            assistant_turn(
                stop_reason="end_turn",
                content=[text_block("Revenue fell to 61000. [1]")],
                usage=_Usage(cache_read_input_tokens=1000),  # lower on the final turn
            ),
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    assert result.cache_read_input_tokens == 9000  # the max, not the last
