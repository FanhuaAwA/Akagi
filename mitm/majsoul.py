from __future__ import annotations

import threading
import traceback
import asyncio
import queue
import time
import heapq
import mitmproxy.http
import mitmproxy.log
import mitmproxy.tcp
import mitmproxy.websocket
from mitmproxy import proxy, options, ctx
from mitmproxy.tools.dump import DumpMaster
from .bridge import MajsoulBridge
from .mitm_abc import ClientWebSocketABC
from .logger import logger

# Because in Majsouls, every flow's message has an id, we need to use one bridge for each flow
activated_flows: list[str] = [] # store all flow.id ([-1] is the recently opened)
majsoul_bridges: dict[str, MajsoulBridge] = {} # store all flow.id -> MajsoulBridge
mjai_messages: queue.Queue[dict] = queue.Queue() # store all messages
action_messages: list[tuple[float, int, dict]] = [] # min-heap of (send_at, seq, command)
action_message_seq = 0
action_messages_lock = threading.Lock()
active_ws_flows: dict[str, mitmproxy.http.HTTPFlow] = {}
proxy_loop: asyncio.AbstractEventLoop | None = None
proxy_addon: "ClientWebSocket" | None = None
drain_timer_handle: asyncio.Handle | None = None
RETRY_DELAY_SECONDS = 0.35


class ClientWebSocket(ClientWebSocketABC):
    def __init__(self):
        self.bridge_lock = threading.Lock()
        pass

    def websocket_start(self, flow: mitmproxy.http.HTTPFlow):
        assert isinstance(flow.websocket, mitmproxy.websocket.WebSocketData)
        global activated_flows, majsoul_bridges, active_ws_flows
        logger.info(f"WebSocket connection opened: {flow.id}")
        if activated_flows and activated_flows[-1] != flow.id:
            self._clear_pending_actions("active websocket flow switched")
        activated_flows.append(flow.id)
        majsoul_bridges[flow.id] = MajsoulBridge()
        active_ws_flows[flow.id] = flow
        self._schedule_next_drain()

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow):
        assert isinstance(flow.websocket, mitmproxy.websocket.WebSocketData)
        global activated_flows, majsoul_bridges
        try:
            if flow.id in activated_flows:
                msg = flow.websocket.messages[-1]
                if msg.from_client:
                    logger.debug(f"<- Message: {msg.content}")
                else: # from server
                    logger.debug(f"-> Message: {msg.content}")
                self.bridge_lock.acquire()
                bridge = majsoul_bridges[flow.id]
                msgs = bridge.parse(msg.content)
                self.bridge_lock.release()
                if msgs is None:
                    self._drain_pending_actions()
                    return
                for m in msgs:
                    mjai_messages.put(m)
                self._drain_pending_actions()
            else:
                logger.error(f"WebSocket message received from unactivated flow: {flow.id}")
        except Exception as e:
            # Release the lock if it is locked
            if self.bridge_lock.locked():
                self.bridge_lock.release()
            logger.error(f"Error: {traceback.format_exc()}")
            logger.error(f"Error: {str(e)}")
            logger.error(f"Error: {e.__traceback__.tb_lineno}")

    def websocket_end(self, flow: mitmproxy.http.HTTPFlow):
        global activated_flows, majsoul_bridges, active_ws_flows
        was_active = bool(activated_flows) and (activated_flows[-1] == flow.id)
        if flow.id in activated_flows:
            logger.info(f"WebSocket connection closed: {flow.id}")
            activated_flows.remove(flow.id)
            del majsoul_bridges[flow.id]
            active_ws_flows.pop(flow.id, None)
            if was_active:
                self._clear_pending_actions("active websocket flow closed")
            if not activated_flows:
                self._cancel_drain_timer()
        else:
            logger.error(f"WebSocket connection closed from unactivated flow: {flow.id}")

    def tick(self):
        self._drain_pending_actions()

    def _drain_pending_actions(self):
        global activated_flows, majsoul_bridges, action_messages, active_ws_flows, action_messages_lock
        if not activated_flows:
            self._cancel_drain_timer()
            return

        flow_id = activated_flows[-1]
        flow = active_ws_flows.get(flow_id)
        bridge = majsoul_bridges.get(flow_id)
        if flow is None or bridge is None:
            self._schedule_next_drain()
            return

        while True:
            with action_messages_lock:
                if not action_messages:
                    break
                now = time.monotonic()
                send_at, _, queued_command = action_messages[0]
                if send_at > now:
                    break
                _, _, queued_command = heapq.heappop(action_messages)
                command = dict(queued_command)

            step_token = command.get("_step_token")
            if step_token is not None and int(getattr(bridge, "latest_action_step", -1)) != int(step_token):
                logger.info(
                    "Dropped stale autoplay action due to step mismatch: "
                    f"queued_step={step_token}, current_step={getattr(bridge, 'latest_action_step', -1)}, command={command}"
                )
                continue

            self.bridge_lock.acquire()
            try:
                payload = bridge.build(command)
            finally:
                self.bridge_lock.release()

            if payload is None:
                logger.warning(f"Unable to build majsoul command for autoplay action: {command}")
                continue

            try:
                ctx.master.commands.call("inject.websocket", flow, False, payload, False)
                logger.debug(f"Injected majsoul command: {command}")
                self._requeue_retry_if_needed(command)
            except Exception as exc:
                logger.error(f"Failed to inject majsoul command {command}: {exc}")
                self._requeue_retry_if_needed(command)
        self._schedule_next_drain()

    def _schedule_next_drain(self):
        global action_messages, action_messages_lock, proxy_loop, drain_timer_handle
        loop = proxy_loop
        if loop is None or loop.is_closed():
            return

        with action_messages_lock:
            if not action_messages:
                if drain_timer_handle is not None:
                    drain_timer_handle.cancel()
                    drain_timer_handle = None
                return
            next_due = float(action_messages[0][0])

        delay = max(0.0, next_due - time.monotonic())
        if drain_timer_handle is not None:
            drain_timer_handle.cancel()
            drain_timer_handle = None

        if delay <= 0.0:
            drain_timer_handle = loop.call_soon(self._drain_pending_actions)
        else:
            drain_timer_handle = loop.call_later(delay, self._drain_pending_actions)

    def _cancel_drain_timer(self):
        global drain_timer_handle
        if drain_timer_handle is not None:
            drain_timer_handle.cancel()
            drain_timer_handle = None

    def _clear_pending_actions(self, reason: str = ""):
        global action_messages, action_messages_lock
        with action_messages_lock:
            if not action_messages:
                return
            action_messages.clear()
        if reason:
            logger.info(f"Cleared pending autoplay action queue: {reason}")
        self._cancel_drain_timer()

    def _requeue_retry_if_needed(self, command: dict) -> None:
        retries_left = int(command.get("_retries_left", 0) or 0)
        if retries_left <= 0:
            return
        retry_command = dict(command)
        retry_command["_retries_left"] = retries_left - 1
        if retry_command.get("type") == "nukidora":
            current_mode = str(retry_command.get("_nukidora_mode") or "operation")
            retry_command["_nukidora_mode"] = "discard" if current_mode == "operation" else "operation"
        due = time.monotonic() + RETRY_DELAY_SECONDS
        global action_messages, action_messages_lock, action_message_seq
        with action_messages_lock:
            seq = action_message_seq
            action_message_seq += 1
            heapq.heappush(action_messages, (due, seq, retry_command))
        logger.debug(f"Scheduled autoplay retry after {RETRY_DELAY_SECONDS:.2f}s: {retry_command}")

async def start_proxy(host, port):
    global proxy_loop, proxy_addon
    opts = options.Options(listen_host=host, listen_port=port)
    master = DumpMaster(
        opts,
        with_termlog=False,
        with_dumper=False,
    )
    addon = ClientWebSocket()
    master.addons.add(addon)
    proxy_loop = asyncio.get_running_loop()
    proxy_addon = addon
    logger.info(f"Starting MITM proxy server at {host}:{port}")
    try:
        await master.run()
    finally:
        addon._cancel_drain_timer()
        proxy_addon = None
        proxy_loop = None
    logger.info("MITM proxy server stopped")
    return master

def stop_proxy():
    ctx.master.shutdown()


def enqueue_action(command: dict, delay: float = 0.0) -> None:
    global action_messages, action_messages_lock, action_message_seq
    if not isinstance(command, dict):
        return
    due = time.monotonic() + max(float(delay), 0.0)
    with action_messages_lock:
        seq = action_message_seq
        action_message_seq += 1
        heapq.heappush(action_messages, (due, seq, dict(command)))
    _schedule_next_drain_threadsafe()


def _schedule_next_drain_threadsafe() -> None:
    global proxy_loop, proxy_addon
    loop = proxy_loop
    addon = proxy_addon
    if loop is None or addon is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(addon._schedule_next_drain)
    except RuntimeError:
        return


def get_latest_self_operation_list() -> list[dict]:
    global activated_flows, majsoul_bridges
    if not activated_flows:
        return []
    flow_id = activated_flows[-1]
    bridge = majsoul_bridges.get(flow_id)
    if bridge is None:
        return []
    return list(bridge.latest_self_operation_list)


def get_latest_self_operation_timing() -> dict:
    global activated_flows, majsoul_bridges
    if not activated_flows:
        return {}
    flow_id = activated_flows[-1]
    bridge = majsoul_bridges.get(flow_id)
    if bridge is None:
        return {}
    return dict(getattr(bridge, "latest_self_operation_timing", {}) or {})


def get_latest_action_step() -> int:
    global activated_flows, majsoul_bridges
    if not activated_flows:
        return -1
    flow_id = activated_flows[-1]
    bridge = majsoul_bridges.get(flow_id)
    if bridge is None:
        return -1
    return int(getattr(bridge, "latest_action_step", -1))
