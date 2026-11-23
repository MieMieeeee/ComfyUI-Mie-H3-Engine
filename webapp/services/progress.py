"""Live render progress, read from ComfyUI's own event stream.

ComfyUI says what it is doing on its ``/ws`` endpoint: which node is
running, how far the sampler has got, when the prompt is finished. The
messages that matter are addressed to the client id that queued the
prompt, so the workbench subscribes with the SAME client id it submits
with.

One subscription for the whole app. Not one per browser tab and not one
per update: the pages read the state this module keeps, and the state
is fed by a single background reader.

What this is NOT: the truth about whether a run succeeded. That still
comes from ComfyUI's own history (see ``runner``), because the event
stream can drop a message. This is the part a person waiting for a
render actually wants - "镜头 3/12, 采样 8/20 步" - and it is honest
about the parts it cannot know ("—" instead of a made-up number).
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse

from webapp.services import comfy, comfy_ws

#: How many prompts are remembered. The list is a log of the recent
#: renders, not an archive.
MAX_TRACKED = 40

#: How many updates one page may fall behind before it misses some.
#: A browser that is not reading must not make the server remember
#: everything for it.
QUEUE_MAX = 32

#: How long a page waits between keep-alive comments.
KEEPALIVE_SECONDS = 15.0

#: How long a finished run stays worth telling a late page about.
#: A page that opens after the render ended gets no event stream - there
#: is nothing live to watch - but the ending is still true. It is not
#: worth telling about forever, though: yesterday's run is history, and
#: the history page is where history belongs.
TERMINAL_SNAPSHOT_SECONDS = 600.0

#: The node classes whose execution means "one more shot is done".
_SHOT_CLASS_MARKERS = ("SegmentSave", "SegmentSave|MieH3")

#: What the author sees for the nodes that matter. Everything else is
#: shown with its own class name - a raw name is not pretty, but it is
#: true.
_NODE_LABELS = {
    "MiniMaxH3ReferenceToVideo": "视频生成",
    "SamplerCustomAdvanced": "采样",
    "KSampler": "采样",
    "KSamplerAdvanced": "采样",
    "SamplerCustom": "采样",
    "MiniMaxH3ChainContext": "上下文衔接",
    "MiniMaxH3ChainSegmentSave": "保存分镜",
    "MiniMaxH3ChainPreflight": "预检",
    "MiniMaxH3ChainReview": "分镜审片",
    "MiniMaxH3ChainAssemble": "合成成片",
    "MiniMaxH3ChainLoopStart": "开始渲染",
    "MiniMaxH3ChainLoopEnd": "收尾",
    "MiniMaxH3ChainCurrent": "读取当前分镜",
    "MiniMaxH3ChainPlanModern|MieH3": "读取计划",
    "MieH3ScriptToPlan": "组装计划",
    "MieH3AssetToSlots": "资产接线",
    "MieH3LoadAsset": "读取资产",
    "MieH3LoadScriptAsset": "读取剧本",
    "UNETLoader": "加载扩散模型",
    "CLIPLoader": "加载文本编码器",
    "VAELoader": "加载 VAE",
    "ModelAttentionBackend": "注意力后端",
}

#: The statuses a prompt can be in while it still wants the event
#: stream.
_LIVE_STATUSES = ("queued", "running")


def node_label(class_type: str) -> str:
    """What a node class is called on the page.

    The engine's own nodes carry a suffix ("|MieH3"): the label is the
    one of the class without it.
    """
    class_type = str(class_type or "")
    if class_type in _NODE_LABELS:
        return _NODE_LABELS[class_type]
    base = class_type.split("|", 1)[0]
    if base in _NODE_LABELS:
        return _NODE_LABELS[base]
    for marker, label in _NODE_LABELS.items():
        if base.endswith(marker):
            return label
    return class_type or "未知节点"


def _is_shot_node(class_type: str) -> bool:
    """Does this node finishing mean one more shot is done?"""
    class_type = str(class_type or "")
    return any(marker in class_type for marker in _SHOT_CLASS_MARKERS)


def _is_sampler_node(class_type: str) -> bool:
    """Is this node the one that reports sampler steps?"""
    return "sampler" in str(class_type or "").lower()


class ProgressHub:
    """The live progress of the renders this app submitted.

    ``connect`` is the seam the tests use: it is called with the client
    id of the prompt to watch and returns an iterator of ComfyUI's
    messages. The default reads the real ``/ws`` endpoint.
    """

    def __init__(self, connect=None):
        self._lock = threading.RLock()
        self._prompts: dict = {}
        self._order: list = []
        self._subscribers: list = []
        self._thread = None
        self._stop = threading.Event()
        self._connect = connect or self._default_connect

    # ------------------------------------------------------------------ #
    # registering a prompt
    # ------------------------------------------------------------------ #

    def register(self, prompt_id: str, client_id: str, **fields) -> dict:
        """Start watching one prompt.

        ``client_id`` is the id the prompt was submitted with: ComfyUI
        addresses the interesting messages to it.
        """
        prompt_id = str(prompt_id or "")
        if not prompt_id:
            return {}
        with self._lock:
            state = self._prompts.get(prompt_id)
            if state is None:
                state = {
                    "prompt_id": prompt_id,
                    "client_id": str(client_id or ""),
                    "run_name": str(fields.get("run_name") or ""),
                    "script": str(fields.get("script") or ""),
                    "title": str(fields.get("title") or ""),
                    "status": "queued",
                    "node": "",
                    "node_label": "",
                    "step": None,
                    "step_total": None,
                    "percent": None,
                    "shot": None,
                    "shot_total": fields.get("shot_total") or None,
                    "shots_done": 0,
                    "message": "已排队",
                    "online": False,
                    "updated_at": time.time(),
                    "nodes": dict(fields.get("nodes") or {}),
                }
                self._prompts[prompt_id] = state
                self._order.append(prompt_id)
                while len(self._order) > MAX_TRACKED:
                    self._prompts.pop(self._order.pop(0), None)
            else:
                state["client_id"] = str(client_id or state.get("client_id") or "")
                state["run_name"] = str(fields.get("run_name")
                                        or state.get("run_name") or "")
                state["title"] = str(fields.get("title") or state.get("title") or "")
                state["script"] = str(fields.get("script") or state.get("script") or "")
                if fields.get("shot_total"):
                    state["shot_total"] = fields["shot_total"]
                if fields.get("nodes"):
                    state["nodes"] = dict(fields["nodes"])
            state["updated_at"] = time.time()
            self._ensure_reader()
        self.publish()
        return self.snapshot(prompt_id)

    def forget(self, prompt_id: str) -> None:
        """Stop watching one prompt (it is over)."""
        with self._lock:
            self._prompts.pop(str(prompt_id or ""), None)
            self._order = [pid for pid in self._order if pid != str(prompt_id or "")]
        self.publish()

    def set_status(self, prompt_id: str, status: str, message: str = "") -> None:
        """The task list's own verdict, folded into the live state.

        The event stream can drop a message; the history cannot. So the
        runner tells the hub what really happened, and the two never
        disagree for long.
        """
        prompt_id = str(prompt_id or "")
        with self._lock:
            state = self._prompts.get(prompt_id)
            if state is None:
                return
            status = str(status or "")
            if status and status != state.get("status"):
                state["status"] = status
                if message:
                    state["message"] = message
                elif status not in _LIVE_STATUSES:
                    state["message"] = _TERMINAL_MESSAGES.get(status, status)
                state["updated_at"] = time.time()
        self.publish()

    # ------------------------------------------------------------------ #
    # reading the state
    # ------------------------------------------------------------------ #

    def active_prompt(self) -> str:
        """The prompt the pages should show: the newest one that lives."""
        with self._lock:
            for prompt_id in reversed(self._order):
                state = self._prompts.get(prompt_id)
                if state and state.get("status") in _LIVE_STATUSES:
                    return prompt_id
            return self._order[-1] if self._order else ""

    def snapshot(self, prompt_id: str = "") -> dict:
        """One prompt's progress, as the pages see it."""
        with self._lock:
            target = str(prompt_id or "") or self.active_prompt()
            state = self._prompts.get(target)
            if state is None:
                return {}
            return _view(state)

    def snapshot_all(self) -> list:
        """Every remembered prompt's progress, newest first."""
        with self._lock:
            return [_view(self._prompts[pid])
                    for pid in reversed(self._order) if pid in self._prompts]

    def terminal_snapshot(self) -> dict:
        """The newest finished run, as a page that arrives late sees it.

        A page that opens after the render ended has no event stream to
        watch: there is nothing live. But the ending is still true, and
        the page has to be able to say it. Only the newest finished run
        is kept, and only for a while - an old ending belongs in the
        history, not in the progress badge.
        """
        now = time.time()
        with self._lock:
            for prompt_id in reversed(self._order):
                state = self._prompts.get(prompt_id)
                if state is None:
                    continue
                if state.get("status") in _LIVE_STATUSES:
                    continue
                if now - float(state.get("updated_at") or 0) \
                        > TERMINAL_SNAPSHOT_SECONDS:
                    continue
                return _view(state)
        return {}

    # ------------------------------------------------------------------ #
    # the pages that watch
    # ------------------------------------------------------------------ #

    def subscribe(self) -> "queue.Queue":
        """A queue that receives every update until it is dropped."""
        channel: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: "queue.Queue") -> None:
        """Let a page go; the reader must not write into it any more."""
        with self._lock:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    def publish(self) -> None:
        """Tell every watcher what the state is now."""
        payload = {
            "prompts": self.snapshot_all(),
            "active": self.active_prompt(),
            "server_time": time.time(),
        }
        with self._lock:
            channels = list(self._subscribers)
        for channel in channels:
            try:
                channel.put_nowait(payload)
            except queue.Full:
                # A page that is not reading must not make the server
                # remember everything for it: the oldest update goes.
                try:
                    channel.get_nowait()
                    channel.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    pass

    def payload(self) -> dict:
        """The current state, as the SSE endpoint sends it."""
        return {
            "prompts": self.snapshot_all(),
            "active": self.active_prompt(),
            # The last finished run, so a page that opens the stream
            # after the end still hears how it went.
            "last": self.terminal_snapshot(),
            "server_time": time.time(),
        }

    # ------------------------------------------------------------------ #
    # the event stream
    # ------------------------------------------------------------------ #

    def handle_event(self, message: dict) -> None:
        """Fold one ComfyUI message into the state.

        Only messages about a prompt this app submitted are used: a
        message about somebody else's prompt says nothing about this
        render, and a late message about the previous render must not
        touch the new one.
        """
        if not isinstance(message, dict):
            return
        kind = str(message.get("type") or "")
        data = message.get("data")
        if not isinstance(data, dict):
            data = {}
        prompt_id = str(data.get("prompt_id") or "")
        if not prompt_id:
            return
        with self._lock:
            state = self._prompts.get(prompt_id)
            if state is None:
                return
            if state.get("status") not in _LIVE_STATUSES:
                # The render is over: late messages must not reopen it.
                return
            changed = self._apply(state, kind, data)
            if changed:
                state["updated_at"] = time.time()
        if changed:
            self.publish()

    def _apply(self, state: dict, kind: str, data: dict) -> bool:
        """One message, applied to one state. Returns True when it moved."""
        if kind == "executing":
            node = data.get("node")
            if node is None:
                state["status"] = "success"
                state["message"] = "渲染完成"
                state["step"] = None
                state["step_total"] = None
                state["percent"] = 100
                if state.get("shot_total"):
                    state["shot"] = state["shot_total"]
                return True
            state["status"] = "running"
            state["node"] = str(node)
            label = node_label(state.get("nodes", {}).get(str(node), ""))
            state["node_label"] = label
            state["message"] = "正在执行 %s" % (label or "节点")
            return True
        if kind == "progress":
            return self._apply_progress(state, data)
        if kind == "progress_state":
            return self._apply_progress_state(state, data)
        if kind == "executed":
            node = str(data.get("node") or "")
            if _is_shot_node(state.get("nodes", {}).get(node, "")):
                state["shots_done"] = int(state.get("shots_done") or 0) + 1
                total = state.get("shot_total")
                if total:
                    state["shot"] = min(int(state["shots_done"]) + 1, int(total))
                else:
                    state["shot"] = int(state["shots_done"])
                return True
            return False
        if kind == "execution_success":
            state["status"] = "success"
            state["message"] = "渲染完成"
            return True
        if kind == "execution_error":
            state["status"] = "error"
            message = (data.get("exception_message")
                       or data.get("exception_type") or "")
            state["message"] = "渲染失败：%s" % message if message else "渲染失败"
            return True
        if kind == "execution_interrupted":
            state["status"] = "interrupted"
            state["message"] = "渲染被中断"
            return True
        if kind.startswith("minimax_h3"):
            # The engine's own events: the review gate and the rest.
            message = str(data.get("status") or data.get("message") or "")
            if message:
                state["message"] = message
                return True
            return False
        return False

    def _apply_progress(self, state: dict, data: dict) -> bool:
        """A ``progress`` message: sampler steps, or a share of a node."""
        node = str(data.get("node") or "")
        try:
            value = float(data.get("value") or 0)
            total = float(data.get("max") or 0)
        except (TypeError, ValueError):
            return False
        if total <= 0:
            return False
        class_type = state.get("nodes", {}).get(node, "")
        if _is_sampler_node(class_type):
            state["step"] = int(value)
            state["step_total"] = int(total)
            state["percent"] = int(round(value * 100.0 / total))
            state["message"] = "采样 %d/%d 步" % (int(value), int(total))
            return True
        state["percent"] = int(round(value * 100.0 / total))
        state["message"] = "%s %d%%" % (node_label(class_type) or "进度",
                                       state["percent"])
        return True

    def _apply_progress_state(self, state: dict, data: dict) -> bool:
        """A ``progress_state`` message: every node's own progress."""
        nodes = data.get("nodes")
        if not isinstance(nodes, dict):
            return False
        active = ""
        for node_id, entry in nodes.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("state") == "running":
                active = str(node_id)
                break
        if not active:
            active = str(state.get("node") or "")
        if not active:
            return False
        entry = nodes.get(active)
        if not isinstance(entry, dict):
            return False
        class_type = state.get("nodes", {}).get(active, "")
        try:
            value = float(entry.get("value") or 0)
            total = float(entry.get("max") or 0)
        except (TypeError, ValueError):
            return False
        if total <= 0:
            return False
        if _is_sampler_node(class_type):
            state["step"] = int(value)
            state["step_total"] = int(total)
        state["percent"] = int(round(value * 100.0 / total))
        state["node"] = active
        state["node_label"] = node_label(class_type)
        state["message"] = "%s %d%%" % (state["node_label"] or "进度",
                                       state["percent"])
        return True

    # ------------------------------------------------------------------ #
    # the background reader
    # ------------------------------------------------------------------ #

    def _default_connect(self, client_id: str, should_stop):
        url = comfy.base_url()
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        else:
            url = "ws://" + url
        url = url.rstrip("/") + "/ws?clientId=" + urllib.parse.quote(client_id)
        return comfy_ws.messages(url, should_stop=should_stop)

    def reader_running(self) -> bool:
        """Is the one reader still running?"""
        with self._lock:
            return bool(self._thread is not None and self._thread.is_alive())

    def _current_client_id(self) -> str:
        with self._lock:
            prompt_id = self.active_prompt()
            state = self._prompts.get(prompt_id)
            return str((state or {}).get("client_id") or "")

    def _ensure_reader(self) -> None:
        """Make sure exactly one reader is running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._read_forever, name="mie-progress-reader", daemon=True)
            self._thread.start()

    def _read_forever(self) -> None:
        """One subscription, reconnecting when the stream drops."""
        backoff = 1.0
        idle_rounds = 0
        while not self._stop.is_set():
            client_id = self._current_client_id()
            if not client_id:
                # Nothing is running: the reader has nothing to listen
                # to, so it stops instead of holding a socket open.
                idle_rounds += 1
                if idle_rounds > 3:
                    return
                self._stop.wait(0.5)
                continue
            idle_rounds = 0

            def should_stop(client_id=client_id) -> bool:
                return (self._stop.is_set()
                        or self._current_client_id() != client_id)

            try:
                for message in self._connect(client_id, should_stop):
                    if self._stop.is_set():
                        break
                    self.handle_event(message)
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001 - a dropped stream is not fatal
                if isinstance(exc, comfy_ws.WebSocketUnavailable) \
                        and "aiohttp" in str(exc):
                    # No websocket client: the rest of the workbench is
                    # fine, so stop trying instead of failing forever.
                    self._mark_offline(str(exc))
                    return
                self._mark_offline()
            if self._current_client_id() != client_id:
                continue    # a new prompt took over: reconnect at once
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)

    def _mark_offline(self, reason: str = "") -> None:
        """The stream is not there: say so, do not pretend."""
        with self._lock:
            for state in self._prompts.values():
                if state.get("status") in _LIVE_STATUSES:
                    state["online"] = False
                    state["message"] = reason or "连不上 ComfyUI 的事件流，进度可能不准"
        self.publish()

    def stop(self) -> None:
        """Stop the reader (the app is going down)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None


#: What a finished run is called, when the history knows better than
#: the event stream.
_TERMINAL_MESSAGES = {
    "success": "渲染完成",
    "error": "渲染失败",
    "interrupted": "渲染被中断",
    "partial": "只做出一部分",
}


def _view(state: dict) -> dict:
    """One state, as the pages see it."""
    return {
        "prompt_id": state.get("prompt_id") or "",
        "run_name": state.get("run_name") or "",
        "script": state.get("script") or "",
        "title": state.get("title") or "",
        "status": state.get("status") or "",
        "node": state.get("node") or "",
        "node_label": state.get("node_label") or "",
        "step": state.get("step"),
        "step_total": state.get("step_total"),
        "percent": state.get("percent"),
        "shot": state.get("shot"),
        "shot_total": state.get("shot_total"),
        "message": state.get("message") or "",
        "online": bool(state.get("online")),
        "updated_at": state.get("updated_at") or 0,
    }


#: The one hub this app uses.
hub = ProgressHub()


def register(prompt_id: str, client_id: str, **fields) -> dict:
    return hub.register(prompt_id, client_id, **fields)


def snapshot(prompt_id: str = "") -> dict:
    return hub.snapshot(prompt_id)


def snapshot_all() -> list:
    return hub.snapshot_all()


def terminal_snapshot() -> dict:
    """The newest finished run, for a page that arrives too late."""
    return hub.terminal_snapshot()


def set_status(prompt_id: str, status: str, message: str = "") -> None:
    hub.set_status(prompt_id, status, message)


def forget(prompt_id: str) -> None:
    hub.forget(prompt_id)


def payload() -> dict:
    return hub.payload()


def subscribe() -> "queue.Queue":
    return hub.subscribe()


def unsubscribe(channel: "queue.Queue") -> None:
    hub.unsubscribe(channel)


def publish() -> None:
    hub.publish()


def sse_event(data: dict) -> str:
    """One Server-Sent Event."""
    return "data: %s\n\n" % json.dumps(data, ensure_ascii=False)
