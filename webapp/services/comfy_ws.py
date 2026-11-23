"""Read ComfyUI's own event stream.

ComfyUI publishes what it is doing on ``/ws``: which node is running,
how far the sampler has got, when the prompt is finished. The messages
that matter are addressed to the client id that queued the prompt, so
the workbench subscribes with the SAME client id it submits with.

The client is aiohttp's: ComfyUI already runs on an interpreter that
has it (the engine's requirements say so), so there is no reason to
write a second websocket parser - aiohttp does the handshake, the
framing, the ping/pong, the TLS and the bounded shutdown.

aiohttp is asynchronous and the workbench's reader is a thread, so
:func:`messages` runs the stream in its own event loop and hands the
messages over through a queue.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading

#: The biggest text message we accept. ComfyUI's progress events are
#: small; a bigger one is not something we need.
MAX_MESSAGE = 8 * 1024 * 1024

#: How often the connection is checked for a dead peer.
HEARTBEAT = 20.0


class WebSocketUnavailable(RuntimeError):
    """The event stream could not be reached or refused the connection."""


def aiohttp_available() -> bool:
    """Is aiohttp there? It comes with ComfyUI, but the workbench may
    also run on an interpreter that has not."""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return True


async def astream(url: str, should_stop=None):
    """Yield the JSON messages on ``url`` (an async generator).

    ``should_stop`` is asked between messages; when it says yes the
    connection is closed and the generator stops. That is how a new
    prompt (with a new client id) takes the subscription over.
    """
    try:
        from aiohttp import (ClientError, ClientSession, WSMsgType,
                             WSServerHandshakeError)
    except ImportError as exc:  # pragma: no cover - ComfyUI ships it
        raise WebSocketUnavailable(
            "没有 aiohttp：读不了 ComfyUI 的事件流（ComfyUI 自带它，"
            "工作台用的是同一个解释器）") from exc

    async with ClientSession() as session:
        try:
            async with session.ws_connect(url, heartbeat=HEARTBEAT,
                                          max_msg_size=MAX_MESSAGE) as socket:
                async for message in socket:
                    if should_stop is not None and should_stop():
                        break
                    if message.type == WSMsgType.TEXT:
                        try:
                            yield json.loads(message.data)
                        except ValueError:
                            continue
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING,
                                          WSMsgType.CLOSED):
                        break
                    elif message.type == WSMsgType.ERROR:
                        raise WebSocketUnavailable("%s" % (socket.exception(),))
        except WSServerHandshakeError as exc:
            raise WebSocketUnavailable(
                "ComfyUI 的事件流拒绝了连接：%s" % (exc,)) from exc
        except (ClientError, OSError) as exc:
            # A connection that failed is NOT progress: the reader has
            # to hear about it.
            raise WebSocketUnavailable(
                "连不上 ComfyUI 的事件流：%s" % (exc,)) from exc


def messages(url: str, should_stop=None):
    """The messages on ``url``, as a plain iterator.

    The stream runs in its own event loop in a worker thread; the
    iterator ends when the stream does, or when ``should_stop`` says so.
    """
    sink: "queue.Queue" = queue.Queue(maxsize=256)

    def offer(item) -> None:
        """Hand one thing over to the reader (waiting for room)."""
        while True:
            if should_stop is not None and should_stop():
                return
            try:
                sink.put(item, timeout=0.5)
                return
            except queue.Full:
                continue

    async def pump():
        async for message in astream(url, should_stop):
            offer(message)

    def run():
        try:
            asyncio.run(pump())
        except Exception as exc:  # noqa: BLE001 - handed to the reader
            # A connection that failed is NOT progress: the reader has
            # to see it, or it would sit and wait for a stream that is
            # not there.
            offer(exc)
            return
        offer(None)

    thread = threading.Thread(target=run, daemon=True, name="mie-ws-reader")
    thread.start()

    def iterate():
        while True:
            if should_stop is not None and should_stop():
                return
            try:
                item = sink.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    return iterate()
