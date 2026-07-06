"""Tests: thinking streams LIVE (not dumped all at once when it ends).

The core UX bug: on_thinking only accumulated text in memory, and the UI showed
nothing during the (possibly 10-25s) thinking phase — it looked frozen. Now
on_thinking creates an EXPANDED Collapsible on the first chunk and appends each
chunk as it arrives, so the user sees reasoning grow in real time.
"""
import pytest

from coderio.cli.tui import CoderioTUI
from textual.widgets import Collapsible, Static


def _think_body_texts(app) -> list[str]:
    """All Static texts inside Collapsibles (the body, not the title)."""
    return [str(w.content) for w in app.query("Collapsible Static")]


@pytest.mark.asyncio
async def test_first_thinking_chunk_creates_expanded_block():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("我需要先看看代码")
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1
        assert cols[0].collapsed is False  # EXPANDED — visible immediately
        assert "我需要先看看代码" in _think_body_texts(app)[1]


@pytest.mark.asyncio
async def test_subsequent_chunks_append_live():
    """Multiple thinking chunks must accumulate into the SAME block, growing."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("第一段")
        await pilot.pause()
        app.on_thinking("第二段")
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1  # still ONE block, not two
        body = _think_body_texts(app)[1]
        assert "第一段" in body and "第二段" in body


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
        await pilot.pause()
        app.on_token("最终回答")  # triggers _flush_round_thinking
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
        await pilot.pause()
        app.on_token("回答")
        await pilot.pause()
        assert app._live_think_body is None
        assert app._round_thinking == ""
        # second round
        app.on_step_start()
        await pilot.pause()
        app.on_thinking("第二轮思考")
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 2  # two separate blocks, not merged
