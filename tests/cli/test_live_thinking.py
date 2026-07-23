"""Tests: thinking streams LIVE (not dumped all at once when it ends).

The core UX bug: on_thinking only accumulated text in memory, and the UI showed
nothing during the (possibly 10-25s) thinking phase — it looked frozen. Now
on_thinking creates an EXPANDED Collapsible on the first chunk and appends each
chunk as it arrives, so the user sees reasoning grow in real time.
"""

import pytest
from textual.widgets import Collapsible

from coderio.cli.tui import CoderioTUI


def _think_body_texts(app) -> list[str]:
    """All text content from the app's live thinking body + any Collapsible Statics."""
    out = []
    # The live thinking body is tracked directly
    if app._live_think_body is not None:
        out.append(str(getattr(app._live_think_body, "content", "")))
    # Also walk all Statics in history for folded blocks
    for w in app.query("Static"):
        c = str(getattr(w, "content", ""))
        if c:
            out.append(c)
    return out


@pytest.mark.asyncio
async def test_first_thinking_chunk_creates_expanded_block():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("我需要先看看代码")
        app._drain_render_queue()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1
        assert cols[0].collapsed is False  # EXPANDED — visible immediately
        # The thinking text must be somewhere in the Collapsible's content
        all_texts = _think_body_texts(app)
        assert any("我需要先看看代码" in t for t in all_texts), f"thinking text not in {all_texts}"


@pytest.mark.asyncio
async def test_subsequent_chunks_append_live():
    """Multiple thinking chunks must accumulate into the SAME block, growing."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("第一段")
        app._drain_render_queue()
        # Force a time gap so the throttle allows the second chunk through
        import time as _t

        _t.sleep(0.07)
        app.on_thinking("第二段")
        app._drain_render_queue()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1  # still ONE block, not two
        # Both chunks accumulated in the round thinking buffer
        assert "第一段" in app._round_thinking and "第二段" in app._round_thinking


@pytest.mark.asyncio
async def test_flush_collapses_the_live_block():
    """When thinking ends (first token/tool/finish), the live block collapses
    and its title shows the elapsed time + char count."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("一些思考内容用于测试")
        app._drain_render_queue()
        app.on_token("最终回答")
        app._drain_render_queue()  # triggers _flush_round_thinking
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1
        assert cols[0].collapsed is True  # folded after flush
        title = str(cols[0].title)
        assert "思考" in title
        assert "字" in title  # char count in the title


@pytest.mark.asyncio
async def test_no_thinking_means_no_block():
    """A turn with no thinking chunks must not create an empty Collapsible."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_token("直接回答")
        await pilot.pause()
        assert list(app.query(Collapsible)) == []


@pytest.mark.asyncio
async def test_live_body_cleared_after_flush():
    """After flushing, the live-body tracker resets so the next round starts fresh."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("第一轮思考")
        app._drain_render_queue()
        app.on_token("回答")
        app._drain_render_queue()
        await pilot.pause()
        assert app._live_think_body is None
        assert app._round_thinking == ""
        # second round
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("第二轮思考")
        app._drain_render_queue()
        cols = list(app.query(Collapsible))
        assert len(cols) == 2  # two separate blocks, not merged


@pytest.mark.asyncio
async def test_rapid_thinking_chunks_do_not_fragment():
    """REGRESSION: many thinking chunks arriving within ONE drain window must
    produce ONE Collapsible, not one per chunk.

    This is the exact bug shown in the user's screenshot: 6 tiny thinking
    Collapsibles, each holding an incremental prefix ("The", "The user is",
    "The user is asking", ...) instead of ONE continuously-growing block.

    Root cause (pre-fix): on_thinking checked `_live_think_body is None` to
    decide think_start vs think_update, but `_live_think_body` is set by the
    MAIN thread when it executes think_start — which only happens on the next
    _drain_render_queue tick (~60ms later). So when N chunks arrive in the same
    drain window, each one saw `_live_think_body is None` and queued another
    think_start, mounting N separate Collapsibles.

    Fix: use an agent-thread-local `_round_think_started` flag, set in
    on_thinking itself, so subsequent chunks in the same window queue
    think_update against the not-yet-mounted-but-already-queued widget.
    """
    import time as _t

    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        # Simulate a burst of thinking chunks landing BEFORE the main thread
        # has had a chance to drain (which is exactly what happens in real
        # streaming — chunks arrive in milliseconds, the 60ms timer hasn't
        # fired yet). NO _drain_render_queue() between chunks.
        chunks = ("The ", "user ", "is ", "asking ", "about ", "the ", "project.")
        for i, chunk in enumerate(chunks):
            app.on_thinking(chunk)
            # Allow the throttle to let updates through (one update per >=60ms).
            _t.sleep(0.07)
        app._drain_render_queue()
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1, (
            f"expected ONE thinking Collapsible for a continuous stream, got "
            f"{len(cols)} (fragmentation bug). Bodies: "
            f"{[str(c.children) for c in cols]}"
        )
        # The full accumulated text must be in the live body.
        all_texts = _think_body_texts(app)
        joined = "".join(all_texts)
        assert "The user is asking about the project." in joined, f"full thinking text missing — got {all_texts}"


@pytest.mark.asyncio
async def test_thinking_across_two_rounds_makes_two_blocks():
    """REGRESSION companion: round-boundary still creates separate blocks.

    Confirms the fix didn't over-merge: a NEW round (on_step_start +
    on_tool_end cycle) must still produce a NEW Collapsible, one per round,
    because each round is a separate model.stream() call with its own thinking.
    """
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Round 1
        app.on_step_start()
        app.on_thinking("第一轮的思考内容")
        app._drain_render_queue()
        # A tool call ends round 1's thinking
        app.on_tool_start("read_file", {"path": "x.py"})
        app._drain_render_queue()
        # Round 2
        app.on_step_start()
        app.on_thinking("第二轮的思考内容")
        app._drain_render_queue()
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 2, f"expected 2 blocks (one per round), got {len(cols)}"
