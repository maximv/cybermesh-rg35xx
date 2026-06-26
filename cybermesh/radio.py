"""Background mesh radio BLE connection and message handling."""

from __future__ import annotations

import queue
import re
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from pubsub import pub

from .geo import format_distance, haversine_m
from .i18n import t
from .msgstore import HistoryStore, load_history

BROADCAST_NUM = 0xFFFFFFFF
ROLE_NAMES = {0: "OFF", 1: "PRI", 2: "SEC"}
NODE_SORT_DEFAULT = "default"
NODE_SORT_SNR = "snr"
NODE_SORT_DISTANCE = "distance"
NODE_SORT_MODES = (NODE_SORT_DEFAULT, NODE_SORT_SNR, NODE_SORT_DISTANCE)
NODE_SORT_LABEL_KEYS = {
    NODE_SORT_DEFAULT: "sort.default",
    NODE_SORT_SNR: "sort.snr",
    NODE_SORT_DISTANCE: "sort.distance",
}


def node_sort_label(mode: str) -> str:
    return t(NODE_SORT_LABEL_KEYS.get(mode, "sort.default"))
MESHTASTIC_SERVICE_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"
_BLE_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
BLE_SCAN_TIMEOUT = 10.0
CONNECT_SCAN_TIMEOUT = 10.0
SEND_ACK_TIMEOUT = 90.0

from .chat_types import MAX_MSGS, ChatMessage, SEND_FAILED, SEND_NONE, SEND_PENDING, SEND_SENT


@dataclass
class RadioSnapshot:
    state: str
    error: Optional[str]
    devices: List[BleDevice]
    my_num: Optional[int]
    my_lat: Optional[float]
    my_lon: Optional[float]
    nodes_loading: bool = False
    connect_hint: str = ""


@dataclass
class BleDevice:
    name: str
    address: str


@dataclass
class NodeInfo:
    num: int
    node_id: str
    short: str
    long: str
    snr: Optional[float]
    battery: Optional[int]
    lat: Optional[float]
    lon: Optional[float]
    last_heard: Optional[float]
    distance_m: Optional[float]
    is_favorite: bool = False


@dataclass
class ChannelInfo:
    index: int
    name: str
    role: int
    role_name: str


@dataclass
class DmPeer:
    peer_num: int
    short: str
    last_ts: float
    last_text: str


def _pos_deg_from_fields(pos: dict) -> Tuple[Optional[float], Optional[float]]:
    if not pos:
        return None, None

    def _pick(*keys):
        for key in keys:
            if key in pos and pos[key] is not None:
                return pos[key]
        return None

    lat_i = _pick("latitudeI", "latitude_i", "latitude", "lat")
    lon_i = _pick("longitudeI", "longitude_i", "longitude", "lon")
    if lat_i is None or lon_i is None:
        return None, None
    try:
        lat_i = float(lat_i)
        lon_i = float(lon_i)
    except (TypeError, ValueError):
        return None, None
    if abs(lat_i) > 180 or abs(lon_i) > 180:
        lat, lon = lat_i / 1e7, lon_i / 1e7
    else:
        lat, lon = lat_i, lon_i
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None, None
    return lat, lon


def _pos_deg(node: dict) -> Tuple[Optional[float], Optional[float]]:
    if not node:
        return None, None
    pos = node.get("position")
    if isinstance(pos, dict) and pos:
        lat, lon = _pos_deg_from_fields(pos)
        if lat is not None:
            return lat, lon
    return _pos_deg_from_fields(node)


def _pos_from_obj(obj) -> Tuple[Optional[float], Optional[float]]:
    if obj is None:
        return None, None
    if isinstance(obj, dict):
        return _pos_deg(obj)
    for lat_attr, lon_attr in (
        ("latitude_i", "longitude_i"),
        ("latitudeI", "longitudeI"),
        ("latitude", "longitude"),
    ):
        lat = getattr(obj, lat_attr, None)
        lon = getattr(obj, lon_attr, None)
        if lat is not None and lon is not None:
            return _pos_deg_from_fields({"latitudeI": lat, "longitudeI": lon})
    return None, None


POS_RANK_OVERRIDE = 0
POS_RANK_MANUAL = 1
POS_RANK_EXTERNAL = 2
POS_RANK_INTERNAL = 3
POS_RANK_UNKNOWN = 9


def _position_source_rank(pos: dict) -> int:
    src = pos.get("locationSource") or pos.get("location_source")
    if src is None:
        return POS_RANK_UNKNOWN
    s = str(src).upper()
    if "MANUAL" in s or s == "1":
        return POS_RANK_MANUAL
    if "EXTERNAL" in s or s == "3":
        return POS_RANK_EXTERNAL
    if "INTERNAL" in s or s == "2":
        return POS_RANK_INTERNAL
    return POS_RANK_UNKNOWN


def _position_dicts_from_node(node: dict) -> List[dict]:
    out: List[dict] = []
    pos = node.get("position")
    if isinstance(pos, dict) and pos:
        out.append(pos)
    keys = ("latitudeI", "latitude_i", "latitude", "lat", "longitudeI", "longitude_i", "longitude", "lon")
    if any(k in node for k in keys):
        out.append({k: node[k] for k in keys if k in node})
    return out


def _best_position_from_node(node: dict) -> Tuple[Optional[float], Optional[float], int]:
    best_rank = POS_RANK_UNKNOWN
    best_lat: Optional[float] = None
    best_lon: Optional[float] = None
    for pos in _position_dicts_from_node(node):
        rank = _position_source_rank(pos)
        lat, lon = _pos_deg_from_fields(pos)
        if lat is None:
            continue
        if rank < best_rank or (rank == best_rank and best_lat is None):
            best_lat, best_lon, best_rank = lat, lon, rank
    return best_lat, best_lon, best_rank


def _merge_own_node_dict(node: Optional[dict], my_info: Optional[dict]) -> Optional[dict]:
    """Merge NodeDB entry with getMyNodeInfo(); device position can live only in the latter."""
    if node is None:
        return my_info
    if my_info is None:
        return node
    out = dict(node)
    node_pos = node.get("position") if isinstance(node.get("position"), dict) else {}
    info_pos = my_info.get("position") if isinstance(my_info.get("position"), dict) else {}
    if _pos_deg({"position": info_pos})[0] is not None:
        if _pos_deg({"position": node_pos})[0] is None or _position_source_rank(
            info_pos
        ) <= _position_source_rank(node_pos):
            out["position"] = info_pos
    for key in ("user", "deviceMetrics"):
        if my_info.get(key):
            if key == "user":
                out["user"] = {**(node.get("user") or {}), **(my_info.get("user") or {})}
            else:
                out[key] = my_info[key]
    return out


def _device_fixed_gps_enabled(iface) -> bool:
    local = getattr(iface, "localNode", None)
    if local is None:
        return False
    lc = getattr(local, "localConfig", None)
    if lc is None:
        return False
    pos_cfg = getattr(lc, "position", None)
    if pos_cfg is None:
        return False
    return bool(getattr(pos_cfg, "fixed_position", False))


def _pos_from_position_config(iface) -> Tuple[Optional[float], Optional[float]]:
    """Some firmware keeps the fixed position lat/lon on the position config message."""
    local = getattr(iface, "localNode", None)
    if local is None:
        return None, None
    lc = getattr(local, "localConfig", None)
    pos_cfg = getattr(lc, "position", None) if lc is not None else None
    if pos_cfg is None:
        return None, None
    for lat_attr, lon_attr in (
        ("fixed_position_lat", "fixed_position_lon"),
        ("latitude_i", "longitude_i"),
        ("latitudeI", "longitudeI"),
        ("latitude", "longitude"),
        ("lat", "lon"),
    ):
        lat = getattr(pos_cfg, lat_attr, None)
        lon = getattr(pos_cfg, lon_attr, None)
        if lat:
            deg_lat, deg_lon = _pos_deg_from_fields({"latitudeI": lat, "longitudeI": lon})
            if deg_lat is not None:
                return deg_lat, deg_lon
    return None, None


def _own_node_candidates(iface) -> List[dict]:
    """Every dict that might carry the local node's position, in priority order."""
    out: List[dict] = []
    if hasattr(iface, "getMyNodeInfo"):
        try:
            info = iface.getMyNodeInfo()
        except Exception:  # noqa: BLE001
            info = None
        if isinstance(info, dict):
            out.append(info)
    local = getattr(iface, "localNode", None)
    num = getattr(local, "nodeNum", None) if local is not None else None
    if num is not None:
        by_num = (getattr(iface, "nodesByNum", None) or {}).get(int(num))
        if isinstance(by_num, dict) and by_num not in out:
            out.append(by_num)
        for candidate in (getattr(iface, "nodes", None) or {}).values():
            if isinstance(candidate, dict) and _parse_node_num(candidate.get("num")) == int(num):
                if candidate not in out:
                    out.append(candidate)
    return out


def _read_device_fixed_gps(iface) -> Tuple[Optional[float], Optional[float], int, bool]:
    """Read Fixed GPS flag and coordinates from connected radio (Heltec local node)."""
    fixed = _device_fixed_gps_enabled(iface)
    best_lat: Optional[float] = None
    best_lon: Optional[float] = None
    best_rank = POS_RANK_UNKNOWN
    for node in _own_node_candidates(iface):
        lat, lon, rank = _best_position_from_node(node)
        if lat is not None and (rank < best_rank or best_lat is None):
            best_lat, best_lon, best_rank = lat, lon, rank
    # Fall back to the position config message when the NodeDB has no own position.
    if best_lat is None:
        lat, lon = _pos_from_position_config(iface)
        if lat is not None:
            best_lat, best_lon, best_rank = lat, lon, POS_RANK_MANUAL
    if fixed and best_lat is not None and best_rank > POS_RANK_MANUAL:
        best_rank = POS_RANK_MANUAL
    return best_lat, best_lon, best_rank, fixed


def load_position_override(port_dir: Path) -> Optional[Tuple[float, float]]:
    path = port_dir / "position.txt"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        if len(parts) < 2:
            continue
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if abs(lat) <= 90 and abs(lon) <= 180:
            return lat, lon
    return None


def save_position_override(port_dir: Path, lat: float, lon: float) -> None:
    path = port_dir / "position.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# lat,lon — фиксированная позиция для карты (можно править вручную)\n"
        f"{lat:.6f},{lon:.6f}\n",
        encoding="utf-8",
    )


def _position_source_label(rank: int) -> str:
    if rank == POS_RANK_OVERRIDE:
        return t("pos.override")
    if rank == POS_RANK_MANUAL:
        return t("pos.manual")
    if rank == POS_RANK_EXTERNAL:
        return t("pos.external")
    if rank == POS_RANK_INTERNAL:
        return t("pos.internal")
    return t("pos.unknown")


def _node_is_favorite(node: dict) -> bool:
    return bool(node.get("isFavorite") or node.get("is_favorite"))


def _is_ble_mac(address: str) -> bool:
    return bool(_BLE_MAC_RE.match(address.strip()))


def _normalize_mac(address: str) -> str:
    return address.replace("-", "").replace(":", "").lower()


def _mac_match(a: str, b: str) -> bool:
    return _normalize_mac(a) == _normalize_mac(b)


def _ble_discover(timeout: float = BLE_SCAN_TIMEOUT, want_address: Optional[str] = None):
    from meshtastic.ble_interface import BLEClient

    want = _normalize_mac(want_address) if want_address else None

    def _pick(response) -> list:
        out = []
        for d in response.values():
            dev, adv = d[0], d[1]
            uuids = getattr(adv, "service_uuids", None) or []
            name = (dev.name or "").lower()
            if want and _normalize_mac(dev.address) == want:
                out.append(dev)
            elif MESHTASTIC_SERVICE_UUID in uuids:
                out.append(dev)
            elif "meshtastic" in name:
                out.append(dev)
        return out

    with BLEClient() as client:
        response = client.discover(
            timeout=timeout,
            return_adv=True,
            service_uuids=[MESHTASTIC_SERVICE_UUID],
        )
    found = _pick(response)
    if found:
        return found
    if want:
        with BLEClient() as client:
            response = client.discover(timeout=timeout, return_adv=True)
        for d in response.values():
            dev = d[0]
            if _normalize_mac(dev.address) == want:
                return [dev]
    return []


def _ble_warmup(address: str, log=None) -> Optional[Any]:
    """Scan for the peripheral; return bleak BLEDevice (not just MAC)."""
    log = log or (lambda _m: None)
    from meshtastic.ble_interface import BLEClient

    if not _is_ble_mac(address):
        found = _ble_discover(CONNECT_SCAN_TIMEOUT, want_address=address)
        if found:
            log(f"BLE warmup: found {found[0].address}")
            return found[0]
        return None

    log(f"BLE scan for {address} ({CONNECT_SCAN_TIMEOUT:.0f}s)")
    with BLEClient() as client:
        response = client.discover(timeout=CONNECT_SCAN_TIMEOUT, return_adv=True)
    for d in response.values():
        dev = d[0]
        if _mac_match(dev.address, address):
            log(f"BLE warmup: found {dev.address}")
            return dev
    log("BLE warmup: not advertising")
    return None


def _open_ble_interface(address: str, log=None, ble_device: Optional[Any] = None):
    """Connect after warmup scan; skip full NodeDB on first handshake."""
    from meshtastic.ble_interface import BLEClient, BLEInterface

    log = log or (lambda _m: None)
    device = ble_device
    if device is None and _is_ble_mac(address):
        device = _ble_warmup(address, log=log)
    if device is None:
        raise RuntimeError(t("radio.not_visible", address=address))
    target = getattr(device, "address", None) or address

    class FastBLEInterface(BLEInterface):
        def connect(self, addr=None):  # noqa: ANN001
            connect_target = device
            if connect_target is None:
                connect_target = addr if addr is not None else target
            if isinstance(connect_target, str) and not _is_ble_mac(connect_target):
                return super().connect(connect_target)
            log(
                "BLE GATT connect -> "
                f"{getattr(connect_target, 'address', connect_target)}"
            )
            client = BLEClient(
                connect_target, disconnected_callback=lambda _: self.close()
            )
            client.connect()
            client.discover()
            return client

    return FastBLEInterface(address=target, noNodes=True, timeout=120)


def _parse_node_num(num) -> Optional[int]:
    if num is None:
        return None
    if isinstance(num, str):
        try:
            return int(num, 16) if num.startswith("!") else int(num)
        except ValueError:
            return None
    return int(num)


class RadioManager:
    def __init__(self, log=None, port_dir: Optional[Path] = None) -> None:
        self.events: queue.Queue = queue.Queue()
        self.state = "idle"
        self.error: Optional[str] = None
        self.devices: List[BleDevice] = []
        self.port_dir = port_dir
        self._history: Optional[HistoryStore] = None
        self._device_address: Optional[str] = None
        if port_dir is not None:
            self._history = HistoryStore(port_dir, log=log)
            ch, dm = load_history(port_dir)
            self.channel_msgs = ch
            self.dm_msgs = dm
            if ch or dm:
                total = sum(len(v) for v in ch.values()) + sum(len(v) for v in dm.values())
                (log or (lambda _m: None))(f"loaded {total} messages from history")
        else:
            self.channel_msgs = {}
            self.dm_msgs = {}
        self._interface = None
        self._lock = threading.Lock()
        self._ble_lock = threading.Lock()
        self._subs_registered = False
        self._ble_abort = threading.Event()
        self.log = log or (lambda _msg: None)
        self.my_num: Optional[int] = None
        self.my_lat: Optional[float] = None
        self.my_lon: Optional[float] = None
        self._my_pos_rank = POS_RANK_UNKNOWN
        self.nodes_loading = False
        self._nodes_sync_gen = 0
        self._pending_by_id: Dict[int, Tuple[str, int]] = {}
        self.connect_hint = ""
        if port_dir is not None:
            ov = load_position_override(port_dir)
            if ov:
                self.my_lat, self.my_lon = ov
                self._my_pos_rank = POS_RANK_OVERRIDE
                (log or (lambda _m: None))(
                    f"position.txt: {ov[0]:.5f}, {ov[1]:.5f}"
                )

    def _notify(self, kind: str = "state") -> None:
        self.events.put(kind)

    def _install_queue_hook(self, iface) -> None:
        if getattr(iface, "_mvp_queue_hook", False):
            return
        orig = iface._handleQueueStatusFromRadio
        manager = self

        def wrapped(queue_status) -> None:
            orig(queue_status)
            manager._on_queue_status(queue_status)

        iface._handleQueueStatusFromRadio = wrapped
        iface._mvp_queue_hook = True

    def _outbound_store(self, kind: str, key: int) -> Deque[ChatMessage]:
        if kind == "dm":
            return self._ensure_dm(key)
        return self._ensure_channel(key)

    def _mark_outbound(
        self,
        kind: str,
        key: int,
        *,
        pkt_id: Optional[int] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        dq = self._outbound_store(kind, key)
        target = None
        if pkt_id is not None:
            for msg in reversed(dq):
                if msg.from_me and msg.msg_id == pkt_id:
                    target = msg
                    break
        if target is None:
            for msg in reversed(dq):
                if msg.from_me and msg.send_status == SEND_PENDING:
                    target = msg
                    break
        if target is None:
            return False
        if status is not None:
            target.send_status = status
        if error is not None:
            target.send_error = error
        return True

    @staticmethod
    def _qs_field(queue_status, name: str, default: int = 0) -> int:
        if isinstance(queue_status, dict):
            val = queue_status.get(name, default)
        else:
            val = getattr(queue_status, name, default)
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return default

    def _mark_outbound_by_pkt_id(
        self,
        pkt_id: int,
        *,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        for ch, dq in self.channel_msgs.items():
            for msg in reversed(dq):
                if msg.from_me and msg.msg_id == pkt_id:
                    return self._mark_outbound("channel", ch, pkt_id=pkt_id, status=status, error=error)
        for peer, dq in self.dm_msgs.items():
            for msg in reversed(dq):
                if msg.from_me and msg.msg_id == pkt_id:
                    return self._mark_outbound("dm", peer, pkt_id=pkt_id, status=status, error=error)
        return False

    def _confirm_send_success(
        self,
        kind: Optional[str],
        key: Optional[int],
        pkt_id: int,
    ) -> bool:
        with self._lock:
            self._pending_by_id.pop(pkt_id, None)
        ok = False
        if kind is not None and key is not None:
            ok = self._mark_outbound(kind, key, pkt_id=pkt_id, status=SEND_NONE)
        if not ok:
            ok = self._mark_outbound_by_pkt_id(pkt_id, status=SEND_NONE)
        if ok:
            self.log(f"send confirmed packet={pkt_id:08x}")
            self._persist()
            self._notify("message")
        return ok

    def _apply_queue_status(self, iface, pkt_id: int) -> None:
        qs = getattr(iface, "queueStatus", None)
        if qs is None:
            return
        if self._qs_field(qs, "mesh_packet_id", 0) == pkt_id:
            self._on_queue_status(qs)

    def _track_pending(self, pkt_id: int, kind: str, key: int) -> None:
        with self._lock:
            self._pending_by_id[pkt_id] = (kind, key)
        threading.Thread(
            target=self._pending_timeout, args=(pkt_id, kind, key), daemon=True
        ).start()

    def _pending_timeout(self, pkt_id: int, kind: str, key: int) -> None:
        time.sleep(SEND_ACK_TIMEOUT)
        with self._lock:
            if pkt_id not in self._pending_by_id:
                return
            self._pending_by_id.pop(pkt_id, None)
        if self._mark_outbound(
            kind,
            key,
            pkt_id=pkt_id,
            status=SEND_FAILED,
            error="timeout",
        ):
            self.log(f"send timeout packet={pkt_id:08x}")
            self._persist()
            self._notify("message")

    def _on_queue_status(self, queue_status) -> None:
        pkt_id = self._qs_field(queue_status, "mesh_packet_id", 0)
        if not pkt_id:
            return
        with self._lock:
            meta = self._pending_by_id.pop(pkt_id, None)
        kind: Optional[str] = None
        key: Optional[int] = None
        if meta is not None:
            kind, key = meta
        res = self._qs_field(queue_status, "res", 0)
        if res != 0:
            err = f"device error {res}"
            ok = False
            if kind is not None and key is not None:
                ok = self._mark_outbound(
                    kind, key, pkt_id=pkt_id, status=SEND_FAILED, error=err
                )
            if not ok:
                ok = self._mark_outbound_by_pkt_id(pkt_id, status=SEND_FAILED, error=err)
            if ok:
                with self._lock:
                    self._pending_by_id.pop(pkt_id, None)
                self.log(f"send failed packet={pkt_id:08x} res={res}")
                self._persist()
                self._notify("message")
            return
        self._confirm_send_success(kind, key, pkt_id)

    def _complete_outbound_echo(
        self, *, is_dm: bool, peer: Optional[int], channel: int, text: str, msg_id: Optional[int]
    ) -> bool:
        kind = "dm" if is_dm else "channel"
        key = peer if is_dm and peer is not None else channel
        dq = self._outbound_store(kind, key)
        for msg in reversed(dq):
            if not msg.from_me or msg.send_status != SEND_PENDING:
                continue
            if msg_id is not None and msg.msg_id is not None and msg.msg_id != msg_id:
                continue
            if msg.text != text:
                continue
            with self._lock:
                if msg.msg_id is not None:
                    self._pending_by_id.pop(msg.msg_id, None)
            msg.send_status = SEND_NONE
            if msg_id and msg.msg_id is None:
                msg.msg_id = msg_id
            return True
        return False

    def _ensure_subs(self) -> None:
        if self._subs_registered:
            return
        pub.subscribe(self._on_text, "meshtastic.receive.text")
        pub.subscribe(self._on_position, "meshtastic.receive.position")
        pub.subscribe(self._on_node_updated, "meshtastic.node.updated")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
        self._subs_registered = True

    def _my_node_num(self, iface) -> Optional[int]:
        if self.my_num is not None:
            return self.my_num
        my_info = getattr(iface, "myInfo", None)
        if my_info is not None:
            num = getattr(my_info, "my_node_num", None)
            if num is None and isinstance(my_info, dict):
                num = my_info.get("myNodeNum")
            if num is not None:
                return int(num)
        local = getattr(iface, "localNode", None)
        if local is not None:
            num = getattr(local, "nodeNum", None)
            if num is not None and int(num) >= 0:
                return int(num)
        return None

    def _own_node_dict(self, iface) -> Optional[dict]:
        num = self._my_node_num(iface)
        if num is None:
            return None
        self.my_num = num
        nodes_by_num = getattr(iface, "nodesByNum", None) or {}
        node = nodes_by_num.get(num)
        my_info: Optional[dict] = None
        if hasattr(iface, "getMyNodeInfo"):
            try:
                my_info = iface.getMyNodeInfo()
            except Exception:  # noqa: BLE001
                my_info = None
        node = _merge_own_node_dict(node, my_info if isinstance(my_info, dict) else None)
        if node is None:
            nodes = getattr(iface, "nodes", None) or {}
            for candidate in nodes.values():
                if _parse_node_num(candidate.get("num")) == num:
                    node = _merge_own_node_dict(candidate, my_info if isinstance(my_info, dict) else None)
                    break
        return node

    def _fixed_position_from_iface(self, iface) -> Tuple[Optional[float], Optional[float], int]:
        best_rank = POS_RANK_UNKNOWN
        best_lat: Optional[float] = None
        best_lon: Optional[float] = None

        def _consider(lat: Optional[float], lon: Optional[float], rank: int) -> None:
            nonlocal best_lat, best_lon, best_rank
            if lat is None or lon is None:
                return
            if rank < best_rank or best_lat is None:
                best_lat, best_lon, best_rank = lat, lon, rank

        if self.port_dir is not None:
            ov = load_position_override(self.port_dir)
            if ov:
                _consider(ov[0], ov[1], POS_RANK_OVERRIDE)

        node = self._own_node_dict(iface)
        if node:
            lat, lon, rank = _best_position_from_node(node)
            _consider(lat, lon, rank)

        for attr in ("location", "_location"):
            loc = getattr(iface, attr, None)
            if isinstance(loc, dict):
                lat, lon = _pos_deg(loc)
                _consider(lat, lon, POS_RANK_MANUAL)

        try:
            if hasattr(iface, "getNode"):
                local = iface.getNode("^local")
                num = getattr(local, "nodeNum", None)
                if num is not None:
                    db_node = (getattr(iface, "nodesByNum", None) or {}).get(int(num))
                    if db_node:
                        lat, lon, rank = _best_position_from_node(db_node)
                        _consider(lat, lon, rank)
        except Exception:  # noqa: BLE001
            pass

        lat, lon, rank, fixed = _read_device_fixed_gps(iface)
        if lat is not None:
            if fixed:
                rank = min(rank, POS_RANK_MANUAL)
            _consider(lat, lon, rank)

        return best_lat, best_lon, best_rank

    def _probe_own_fixed_position(self, iface, sync: bool = False) -> None:
        local = getattr(iface, "localNode", None)
        if local is None and hasattr(iface, "getNode"):
            try:
                local = iface.getNode("^local", requestChannels=False, timeout=5 if sync else 20)
            except Exception:  # noqa: BLE001
                local = None
        if local is None:
            return
        try:
            self.log("reading Fixed GPS from device")
            lc = getattr(local, "localConfig", None)
            if lc is not None and hasattr(local, "requestConfig"):
                try:
                    pos_field = lc.DESCRIPTOR.fields_by_name.get("position")
                    pos_cfg = getattr(lc, "position", None)
                    empty = pos_cfg is None or not list(pos_cfg.ListFields())
                    if pos_field is not None and empty:
                        local.requestConfig(pos_field)
                except Exception:  # noqa: BLE001
                    pass
            if hasattr(local, "waitForConfig"):
                local.waitForConfig("position")
            fixed = _device_fixed_gps_enabled(iface)
            if fixed:
                self.log("device: Fixed GPS enabled")
            lat, lon, rank, _ = _read_device_fixed_gps(iface)
            if lat is not None:
                if fixed:
                    rank = min(rank, POS_RANK_MANUAL)
                self._apply_own_position(lat, lon, rank)
                self.log(f"device coords: {lat:.5f}, {lon:.5f}")
            elif fixed:
                self.log("device: Fixed GPS on but no coordinates in NodeDB/config yet")
                self._request_own_position(iface)
        except Exception as exc:  # noqa: BLE001
            self.log(f"device position: {exc}")

    def _request_own_position(self, iface) -> None:
        """Ask the radio to emit its own Position so _on_position can capture it."""
        num = self._my_node_num(iface)
        if num is None:
            return
        for attr in ("sendPosition",):
            fn = getattr(iface, attr, None)
            if not callable(fn):
                continue
            try:
                fn(destinationId=num, wantResponse=True)
                self.log("requested own position from device")
            except Exception as exc:  # noqa: BLE001
                self.log(f"position request failed: {exc}")
            return

    def my_node_labels(self) -> Tuple[str, str]:
        iface = self._iface()
        if iface is None:
            return t("radio.me"), ""
        node = self._own_node_dict(iface)
        if not node:
            return t("radio.me"), ""
        user = node.get("user") or {}
        short = str(user.get("shortName") or user.get("longName") or t("radio.me"))
        long_name = str(user.get("longName") or short)
        return short, long_name

    def _apply_own_position(
        self, lat: Optional[float], lon: Optional[float], source_rank: int = POS_RANK_UNKNOWN
    ) -> bool:
        if lat is None or lon is None:
            return False
        if source_rank > self._my_pos_rank:
            return False
        if source_rank == self._my_pos_rank and self.my_lat == lat and self.my_lon == lon:
            return False
        self.my_lat, self.my_lon = lat, lon
        self._my_pos_rank = source_rank
        if source_rank <= POS_RANK_MANUAL and self.port_dir is not None:
            save_position_override(self.port_dir, lat, lon)
        return True

    def _iface(self):
        with self._lock:
            return self._interface

    def _ensure_channel(self, ch: int) -> Deque[ChatMessage]:
        if ch not in self.channel_msgs:
            self.channel_msgs[ch] = deque(maxlen=MAX_MSGS)
        return self.channel_msgs[ch]

    def _ensure_dm(self, peer: int) -> Deque[ChatMessage]:
        if peer not in self.dm_msgs:
            self.dm_msgs[peer] = deque(maxlen=MAX_MSGS)
        return self.dm_msgs[peer]

    def _persist(self) -> None:
        if self._history is None:
            return
        self._history.schedule_save(
            self.channel_msgs, self.dm_msgs, self._device_address
        )

    def _has_msg_id(self, store: Deque[ChatMessage], msg_id: Optional[int]) -> bool:
        if msg_id is None:
            return False
        return any(m.msg_id == msg_id for m in store)

    def _append_channel(self, channel: int, msg: ChatMessage) -> None:
        dq = self._ensure_channel(channel)
        if self._has_msg_id(dq, msg.msg_id):
            return
        dq.append(msg)
        self._persist()

    def _append_dm(self, peer: int, msg: ChatMessage) -> None:
        dq = self._ensure_dm(peer)
        if self._has_msg_id(dq, msg.msg_id):
            return
        dq.append(msg)
        self._persist()

    def _update_own_position(self) -> None:
        iface = self._iface()
        if iface is None:
            return
        try:
            lat, lon, rank = self._fixed_position_from_iface(iface)
            if self._apply_own_position(lat, lon, rank):
                self._notify("state")
        except Exception:  # noqa: BLE001
            pass

    def _on_position(self, packet, interface=None, **kwargs) -> None:
        iface = interface or self._iface()
        if iface is None:
            return
        from_num = packet.get("from")
        my_num = self._my_node_num(iface)
        if my_num is not None and from_num == my_num:
            decoded = packet.get("decoded") or {}
            pos = decoded.get("position") or {}
            lat, lon = _pos_deg_from_fields(pos)
            rank = _position_source_rank(pos) if pos else POS_RANK_UNKNOWN
            self._apply_own_position(lat, lon, rank)
        self._notify("state")

    def _on_node_updated(self, node=None, interface=None, **kwargs) -> None:
        if not node:
            return
        iface = interface or self._iface()
        if iface is None:
            return
        num = _parse_node_num(node.get("num"))
        my_num = self._my_node_num(iface)
        lat, lon, rank = _best_position_from_node(node)
        if num is not None and my_num is not None and num == my_num:
            self._apply_own_position(lat, lon, rank)
        if lat is not None:
            self._notify("state")

    def _sender_label(self, node_id: Optional[str], from_num: Optional[int]) -> str:
        if from_num == self.my_num:
            return "me"
        iface = self._iface()
        if iface is None:
            return node_id or "?"
        nodes = getattr(iface, "nodes", None) or {}
        if from_num is not None and from_num in nodes:
            user = nodes[from_num].get("user") or {}
            short = user.get("shortName") or user.get("longName")
            if short:
                return str(short)
        if node_id and node_id in nodes:
            user = nodes[node_id].get("user") or {}
            short = user.get("shortName") or user.get("longName")
            if short:
                return str(short)
        if node_id:
            return str(node_id)[:8]
        return "?"

    def _short_for_num(self, num: int) -> str:
        iface = self._iface()
        if iface is None:
            return str(num)
        nodes = getattr(iface, "nodes", None) or {}
        if num in nodes:
            user = nodes[num].get("user") or {}
            return str(user.get("shortName") or user.get("longName") or num)
        return str(num)

    def _parse_reply_id(self, decoded: dict) -> Optional[int]:
        raw = decoded.get("reply_id")
        if raw is None:
            raw = decoded.get("replyId")
        if raw is None:
            return None
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return None
        return val if val != 0 else None

    def reply_target_name(
        self,
        reply_id: int,
        *,
        channel: int = 0,
        is_dm: bool = False,
        peer_num: Optional[int] = None,
    ) -> Optional[str]:
        if is_dm and peer_num is not None:
            store = self.dm_msgs.get(peer_num, deque())
        else:
            store = self.channel_msgs.get(channel, deque())
        for msg in reversed(store):
            if msg.msg_id != reply_id:
                continue
            if msg.from_me:
                if self.my_num is not None:
                    name = self._short_for_num(self.my_num)
                    if name != str(self.my_num):
                        return name
                short, _long = self.my_node_labels()
                if short and short not in ("?", "me"):
                    return short
                return t("chat.you")
            return msg.sender
        return None

    def _on_text(self, packet, interface=None) -> None:
        decoded = packet.get("decoded") or {}
        text = decoded.get("text") or ""
        if not text:
            return
        from_num = packet.get("from")
        to_num = packet.get("to")
        channel = packet.get("channel", 0)
        msg_id = packet.get("id")
        from_me = from_num == self.my_num
        is_dm = (
            to_num is not None
            and to_num != BROADCAST_NUM
            and to_num != 0
            and (to_num == self.my_num or from_me)
        )
        peer = None
        if is_dm:
            peer = to_num if from_me else from_num
        sender = self._sender_label(packet.get("fromId"), from_num)
        reply_id = self._parse_reply_id(decoded)

        # Device echoed our outbound text — confirm only if still pending.
        if from_me and text and self._complete_outbound_echo(
            is_dm=is_dm, peer=peer, channel=channel, text=text, msg_id=msg_id
        ):
            self._persist()
            self._notify("message")
            return

        msg = ChatMessage(
            text=text,
            sender=sender,
            sender_num=from_num,
            channel=channel,
            is_dm=is_dm,
            peer_num=peer,
            msg_id=msg_id,
            reply_id=reply_id,
            from_me=from_me,
        )
        if is_dm and msg.peer_num is not None:
            self._append_dm(msg.peer_num, msg)
        else:
            self._append_channel(channel, msg)
        self._notify("message")

    def _on_connection_lost(self, interface=None, topic=None) -> None:
        with self._lock:
            self.state = "disconnected"
            self._interface = None
        self._notify("state")

    def _close_iface(self, iface) -> None:
        done = threading.Event()

        def _worker() -> None:
            try:
                iface.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                done.set()

        threading.Thread(target=_worker, daemon=True).start()
        if not done.wait(1.5):
            self.log("iface.close() timed out — continuing")

    def start_scan(self) -> None:
        self._ble_abort.clear()
        with self._lock:
            if self.state in ("connecting", "connected", "scanning"):
                return
            self.state = "scanning"
            self.error = None
            self.devices = []
        self._notify()
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            self.log(f"BLE scan start ({BLE_SCAN_TIMEOUT:.0f}s)")
            if self._ble_abort.is_set():
                return
            with self._ble_lock:
                if self._ble_abort.is_set():
                    return
                found = _ble_discover(BLE_SCAN_TIMEOUT)
            devices = [BleDevice(name=(d.name or t("radio.ble_node")), address=d.address) for d in found]
            self.log(f"BLE scan found {len(devices)}: " + ", ".join(d.address for d in devices))
            with self._lock:
                if self.state in ("connecting", "connected"):
                    return
                self.devices = devices
                self.state = "scan_done" if devices else "no_devices"
        except Exception as exc:  # noqa: BLE001
            self.log(f"BLE scan error: {exc}")
            with self._lock:
                if self.state in ("connecting", "connected"):
                    return
                self.error = str(exc)
                self.state = "error"
        self._notify()

    def _wait_scan_finish(self, timeout: float = 12.0) -> None:
        """BlueZ handles one BLE client; let an in-flight scan finish before connect."""
        self._ble_abort.set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self.state != "scanning":
                    break
            time.sleep(0.05)
        self._ble_abort.clear()

    def _set_connect_hint(self, hint: str) -> None:
        with self._lock:
            self.connect_hint = hint
        self._notify()

    def connect(self, address: str) -> None:
        self._wait_scan_finish()
        with self._lock:
            if self.state in ("connecting", "connected"):
                self.log(f"connect ignored (state={self.state})")
                return
            self.state = "connecting"
            self.error = None
            self.connect_hint = t("hint.scanning_ble")
            self._device_address = address
            self.nodes_loading = False
            self._nodes_sync_gen += 1
        self._notify()
        threading.Thread(target=self._connect_worker, args=(address,), daemon=True).start()
        threading.Thread(target=self._connect_watchdog, args=(address,), daemon=True).start()

    def _connect_watchdog(self, address: str) -> None:
        time.sleep(25)
        with self._lock:
            still = self.state == "connecting"
        if still:
            self.log(f"connect to {address} still pending >25s")

    def _start_nodes_sync(self, iface) -> None:
        self._nodes_sync_gen += 1
        gen = self._nodes_sync_gen
        threading.Thread(
            target=self._nodes_sync_worker, args=(iface, gen), daemon=True
        ).start()

    def _nodes_sync_worker(self, iface, gen: int) -> None:
        try:
            time.sleep(0.3)
            with self._lock:
                if gen != self._nodes_sync_gen or self._interface is not iface:
                    return
            if self._ble_abort.is_set():
                return
            self.log("node DB sync start")
            iface.noNodes = False
            iface.configId = None
            iface._startConfig()
            deadline = time.time() + 120
            while time.time() < deadline:
                if self._ble_abort.is_set():
                    return
                with self._lock:
                    if gen != self._nodes_sync_gen or self._interface is not iface:
                        return
                nodes_by_num = getattr(iface, "nodesByNum", None) or {}
                my_num = self._my_node_num(iface)
                own = nodes_by_num.get(my_num) if my_num else None
                if iface.myInfo and (len(nodes_by_num) > 0 or own is not None):
                    time.sleep(0.8)
                    break
                time.sleep(0.25)
            self._update_own_position()
            self._probe_own_fixed_position(iface)
            self._update_own_position()
            count = len(getattr(iface, "nodes", None) or {})
            lat, lon = self.my_lat, self.my_lon
            if lat is not None and lon is not None:
                self.log(f"own position: {lat:.5f}, {lon:.5f} ({_position_source_label(self._my_pos_rank)})")
            self.log(f"node DB sync done ({count} nodes)")
        except Exception as exc:  # noqa: BLE001
            self.log(f"node DB sync error: {exc}")
        finally:
            with self._lock:
                if gen == self._nodes_sync_gen:
                    self.nodes_loading = False
            self._notify("state")

    def _connect_worker(self, address: str) -> None:
        with self._lock:
            old = self._interface
            self._interface = None
        if old is not None:
            self._close_iface(old)
        if self._ble_abort.is_set():
            with self._lock:
                if self.state == "connecting":
                    self.state = "idle"
            return
        try:
            self._ensure_subs()
            direct = _is_ble_mac(address)
            self.log(
                f"connecting BLE -> {address}"
                + (" (scan+warmup, noNodes)" if direct else " (scan+config)")
            )
            with self._ble_lock:
                if self._ble_abort.is_set():
                    raise RuntimeError("connect aborted")
                ble_device = _ble_warmup(address, log=self.log) if direct else None
                if direct and not ble_device:
                    raise RuntimeError(t("radio.not_visible", address=address))
                self._set_connect_hint(t("hint.connecting"))
                iface = (
                    _open_ble_interface(address, log=self.log, ble_device=ble_device)
                    if direct
                    else None
                )
                if iface is None:
                    from meshtastic.ble_interface import BLEInterface

                    iface = BLEInterface(address=address, noNodes=True, timeout=120)
            self._update_own_position()
            self._install_queue_hook(iface)
            with self._lock:
                self._interface = iface
                self.state = "connected"
                self.nodes_loading = True
                self.connect_hint = ""
            self.log("connect OK — chat ready, loading nodes in background")
            self._notify()
            self._start_nodes_sync(iface)
            return
        except Exception as exc:  # noqa: BLE001
            err = str(exc).strip() or repr(exc) or type(exc).__name__
            self.log(f"connect error [{type(exc).__name__}]: {err}")
            for line in traceback.format_exc().strip().splitlines()[-5:]:
                self.log(line)
            hint = ""
            low = err.lower()
            if "not found" in low or "not visible" in low or "не виден" in low:
                hint = t("err.not_visible_hint")
            elif "timed out" in low or "timeout" in low:
                hint = t("err.timeout_hint")
            elif "pair" in low or "auth" in low:
                hint = t("err.pair_hint", address=address)
            with self._lock:
                self.error = (err + hint) if err else (t("err.ble_generic") + hint)
                self.state = "error"
                self._interface = None
                self.connect_hint = ""
        self._notify()

    def disconnect(self) -> None:
        self._ble_abort.set()
        with self._lock:
            iface = self._interface
            self._interface = None
            self.state = "idle"
            self.nodes_loading = False
            self._nodes_sync_gen += 1
            self._pending_by_id.clear()
        if iface is not None:
            self._close_iface(iface)
        if self._history is not None:
            ch = self.channel_msgs
            dm = self.dm_msgs
            addr = self._device_address
            hist = self._history

            def _flush() -> None:
                try:
                    hist.flush_now(ch, dm, addr)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"history flush on disconnect: {exc}")

            threading.Thread(target=_flush, daemon=True).start()
        self._notify()

    def channel_messages(self, channel: int) -> List[ChatMessage]:
        return list(self.channel_msgs.get(channel, []))

    def refresh_position(self, sync: bool = False) -> None:
        self._update_own_position()
        iface = self._iface()
        if iface is None:
            return
        need_probe = self.my_lat is None or self._my_pos_rank > POS_RANK_MANUAL
        if sync:
            if need_probe:
                self._probe_own_fixed_position(iface, sync=True)
            self._update_own_position()
            return

        if not need_probe:
            return

        def _work() -> None:
            self._probe_own_fixed_position(iface, sync=False)
            self._update_own_position()

        threading.Thread(target=_work, daemon=True).start()

    def map_anchor(self) -> Tuple[Optional[float], Optional[float], bool]:
        """Map center: own GPS if available, else centroid of positioned mesh nodes."""
        self._update_own_position()
        if self.my_lat is not None and self.my_lon is not None:
            return self.my_lat, self.my_lon, True
        coords = [
            (n.lat, n.lon)
            for n in self.node_list()
            if n.lat is not None and n.lon is not None
        ]
        if not coords:
            return None, None, False
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        return lat, lon, False

    def map_nodes(self) -> List[NodeInfo]:
        return [n for n in self.node_list() if n.lat is not None and n.lon is not None]

    def positioned_node_count(self) -> int:
        return len(self.map_nodes())

    def dm_messages(self, peer_num: int) -> List[ChatMessage]:
        return list(self.dm_msgs.get(peer_num, []))

    def send_text(
        self,
        text: str,
        channel_index: int = 0,
        reply_id: Optional[int] = None,
    ) -> Optional[str]:
        iface = self._iface()
        if iface is None:
            return t("radio.not_connected")
        msg = ChatMessage(
            text=text,
            sender="me",
            channel=channel_index,
            from_me=True,
            send_status=SEND_PENDING,
            reply_id=reply_id,
        )
        self._append_channel(channel_index, msg)
        self._notify("message")
        dq = self.channel_msgs.get(channel_index)
        try:
            kwargs = {"channelIndex": channel_index}
            if reply_id is not None:
                kwargs["replyId"] = reply_id
            pkt = iface.sendText(text, **kwargs)
            pkt_id = int(getattr(pkt, "id", 0) or 0)
            if dq and pkt_id:
                dq[-1].msg_id = pkt_id
                self._track_pending(pkt_id, "channel", channel_index)
                self._apply_queue_status(iface, pkt_id)
            self._persist()
            self._notify("message")
            return None
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            if dq:
                dq[-1].send_status = SEND_FAILED
                dq[-1].send_error = err
            self._persist()
            self._notify("message")
            return err

    def send_dm(
        self,
        text: str,
        peer_num: int,
        reply_id: Optional[int] = None,
    ) -> Optional[str]:
        iface = self._iface()
        if iface is None:
            return t("radio.not_connected")
        msg = ChatMessage(
            text=text,
            sender="me",
            is_dm=True,
            peer_num=peer_num,
            from_me=True,
            send_status=SEND_PENDING,
            reply_id=reply_id,
        )
        self._append_dm(peer_num, msg)
        self._notify("message")
        dq = self.dm_msgs.get(peer_num)
        try:
            kwargs = {"destinationId": peer_num}
            if reply_id is not None:
                kwargs["replyId"] = reply_id
            pkt = iface.sendText(text, **kwargs)
            pkt_id = int(getattr(pkt, "id", 0) or 0)
            if dq and pkt_id:
                dq[-1].msg_id = pkt_id
                self._track_pending(pkt_id, "dm", peer_num)
                self._apply_queue_status(iface, pkt_id)
            self._persist()
            self._notify("message")
            return None
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            if dq:
                dq[-1].send_status = SEND_FAILED
                dq[-1].send_error = err
            self._persist()
            self._notify("message")
            return err

    def drop_message(self, msg: ChatMessage) -> None:
        """Remove a specific message instance (used when retrying a failed send)."""
        store = self.dm_msgs.get(msg.peer_num) if msg.is_dm else self.channel_msgs.get(msg.channel)
        if store is None:
            return
        kept = [m for m in store if m is not msg]
        if len(kept) != len(store):
            store.clear()
            store.extend(kept)
            self._persist()
            self._notify("message")

    @staticmethod
    def reply_quote(sender: str, text: str) -> str:
        preview = text.replace("\n", " ")[:120]
        return f"> {sender}: {preview}\n"

    def _node_dict_for_num(self, iface, num: int) -> Optional[dict]:
        nodes_by_num = getattr(iface, "nodesByNum", None) or {}
        node = nodes_by_num.get(num)
        if node is not None:
            return node
        for candidate in (getattr(iface, "nodes", None) or {}).values():
            if _parse_node_num(candidate.get("num")) == num:
                return candidate
        return None

    def _iter_mesh_nodes(self, iface) -> List[Tuple[str, dict]]:
        out: List[Tuple[str, dict]] = []
        seen: set[int] = set()
        for node_id, node in (getattr(iface, "nodes", None) or {}).items():
            num = _parse_node_num(node.get("num"))
            if num is not None:
                seen.add(num)
            out.append((str(node_id), node))
        for num, node in (getattr(iface, "nodesByNum", None) or {}).items():
            if num in seen:
                continue
            user = node.get("user") or {}
            node_id = str(user.get("id") or f"!{int(num):08x}")
            out.append((node_id, node))
        return out

    def _build_node_info(self, node: dict, node_id: str = "") -> Optional[NodeInfo]:
        user = node.get("user") or {}
        num = _parse_node_num(node.get("num") or user.get("id"))
        if num is None:
            return None
        short = str(user.get("shortName") or user.get("longName") or str(num))
        long_name = str(user.get("longName") or short)
        lat, lon = _pos_deg(node)
        dist = None
        if lat is not None and self.my_lat is not None and self.my_lon is not None:
            dist = haversine_m(self.my_lat, self.my_lon, lat, lon)
        snr = node.get("snr")
        batt = (node.get("deviceMetrics") or {}).get("batteryLevel")
        lh = node.get("lastHeard")
        if not node_id and "id" in user:
            node_id = str(user["id"])
        return NodeInfo(
            num=num,
            node_id=node_id,
            short=short,
            long=long_name,
            snr=snr,
            battery=batt,
            lat=lat,
            lon=lon,
            last_heard=lh,
            distance_m=dist,
            is_favorite=_node_is_favorite(node),
        )

    def get_node(self, num: int) -> Optional[NodeInfo]:
        iface = self._iface()
        if iface is None:
            return None
        self._update_own_position()
        if num == self.my_num:
            node = self._own_node_dict(iface)
        else:
            node = self._node_dict_for_num(iface, num)
        if node is None:
            return None
        user = node.get("user") or {}
        node_id = str(user.get("id") or "")
        info = self._build_node_info(node, node_id)
        if info is not None and num == self.my_num and (info.lat is None or info.lon is None):
            lat, lon, _rank = self._fixed_position_from_iface(iface)
            if lat is None:
                lat, lon = self.my_lat, self.my_lon
            if lat is not None and lon is not None:
                info.lat = lat
                info.lon = lon
        return info

    def node_detail_lines(self, num: int) -> List[str]:
        iface = self._iface()
        raw = self._node_dict_for_num(iface, num) if iface else None
        info = self.get_node(num)
        if info is None:
            short = self._short_for_num(num)
            if short != str(num):
                return [t("nd.node", short=short), f"Num: {num}", "", t("nd.no_details")]
            return [t("nd.not_found")]

        node_id = info.node_id or f"!{info.num:08x}"
        lines = [
            f"Short: {info.short}",
            f"Long: {info.long}",
            f"ID: {node_id}",
            f"Num: {info.num}",
        ]
        if info.is_favorite:
            lines.append(t("nd.favorite_yes"))
        if num == self.my_num and iface is not None and _device_fixed_gps_enabled(iface):
            lines.append(t("nd.fixed_gps_on"))
        if info.distance_m is not None:
            lines.append(t("nd.distance", val=format_distance(info.distance_m)))
        if info.lat is not None and info.lon is not None:
            lines.append(t("nd.lat", val=f"{info.lat:.5f}"))
            lines.append(t("nd.lon", val=f"{info.lon:.5f}"))
            if num == self.my_num:
                lines.append(t("nd.source", val=_position_source_label(self._my_pos_rank)))
        else:
            lines.append(t("nd.no_position"))
        if info.snr is not None:
            lines.append(f"SNR: {info.snr}")
        if info.battery is not None:
            lines.append(t("nd.battery", val=info.battery))
        if info.last_heard:
            heard = time.strftime("%d.%m %H:%M", time.localtime(info.last_heard))
            lines.append(t("nd.heard", val=heard))
        if raw:
            hops = raw.get("hopsAway")
            if hops is not None:
                lines.append(t("nd.hops", val=hops))
            dm = raw.get("deviceMetrics") or {}
            voltage = dm.get("voltage")
            if voltage:
                lines.append(t("nd.voltage", val=f"{float(voltage):.2f}"))
            ch_util = dm.get("channelUtilization")
            if ch_util is not None:
                lines.append(t("nd.airtime", val=ch_util))
            user = raw.get("user") or {}
            role = user.get("role")
            if role is not None:
                lines.append(t("nd.role", val=role))
            hw = user.get("hwModel")
            if hw is not None:
                lines.append(f"HW: {hw}")
        return lines

    @staticmethod
    def _fmt_field_value(val: Any) -> str:
        if isinstance(val, float):
            return f"{val:.6g}"
        if isinstance(val, dict):
            parts = [f"{k}={RadioManager._fmt_field_value(v)}" for k, v in val.items()]
            return "{" + ", ".join(parts) + "}"
        if isinstance(val, list):
            return "[" + ", ".join(RadioManager._fmt_field_value(v) for v in val) + "]"
        return str(val)

    def _append_raw_section(self, lines: List[str], title: str, data: dict) -> None:
        if not data:
            return
        lines.append("")
        lines.append(f"--- {title} ---")
        for key in sorted(data.keys()):
            lines.append(f"{key}: {self._fmt_field_value(data[key])}")

    def my_node_detail_lines(self) -> List[str]:
        iface = self._iface()
        self._update_own_position()
        num = self.my_num
        if iface is not None and num is None:
            num = self._my_node_num(iface)
        if num is None:
            lines = [t("nd.my_undefined"), "", t("nd.connect_ble")]
            if self.my_lat is not None and self.my_lon is not None:
                lines.extend([
                    "",
                    t("nd.coords_local"),
                    t("nd.lat", val=f"{self.my_lat:.6f}"),
                    t("nd.lon", val=f"{self.my_lon:.6f}"),
                    t("nd.source", val=_position_source_label(self._my_pos_rank)),
                ])
            if self.port_dir is not None:
                pos_file = self.port_dir / "position.txt"
                if pos_file.exists():
                    lines.append(t("nd.file", name=pos_file.name))
            return lines

        lines = self.node_detail_lines(num)
        if self.my_lat is not None and self.my_lon is not None:
            lines.extend([
                "",
                t("nd.coords_map"),
                t("nd.lat", val=f"{self.my_lat:.6f}"),
                t("nd.lon", val=f"{self.my_lon:.6f}"),
                t("nd.source", val=_position_source_label(self._my_pos_rank)),
            ])
        else:
            lines.append("")
            lines.append(t("nd.coords_map_none"))

        raw = self._own_node_dict(iface) if iface else None
        if raw:
            self._append_raw_section(lines, "position", raw.get("position") or {})
            self._append_raw_section(lines, "user", raw.get("user") or {})
            self._append_raw_section(lines, "deviceMetrics", raw.get("deviceMetrics") or {})
            extra = {
                k: raw[k]
                for k in raw
                if k not in ("position", "user", "deviceMetrics", "num")
            }
            self._append_raw_section(lines, t("nd.misc_nodedb"), extra)

        if iface is not None:
            for attr in ("location", "_location"):
                loc = getattr(iface, attr, None)
                if isinstance(loc, dict) and loc:
                    self._append_raw_section(lines, f"iface.{attr}", loc)
            if _device_fixed_gps_enabled(iface):
                local = getattr(iface, "localNode", None)
                lc = getattr(local, "localConfig", None) if local else None
                pos_cfg = getattr(lc, "position", None) if lc else None
                if pos_cfg is not None:
                    cfg = {}
                    for key in ("fixed_position", "gps_enabled", "gps_update_interval"):
                        if hasattr(pos_cfg, key):
                            cfg[key] = getattr(pos_cfg, key)
                    self._append_raw_section(lines, "config.position (Heltec)", cfg)

        return lines

    def node_list(self) -> List[NodeInfo]:
        iface = self._iface()
        if iface is None:
            return []
        self._update_own_position()
        out: List[NodeInfo] = []
        for node_id, node in self._iter_mesh_nodes(iface):
            built = self._build_node_info(node, node_id)
            if built is None:
                continue
            if self.my_num is not None and built.num == self.my_num:
                continue
            out.append(built)
        out.sort(
            key=lambda n: (
                not n.is_favorite,
                n.distance_m is None,
                n.distance_m if n.distance_m is not None else 1e12,
                n.short.lower(),
            )
        )
        return out

    @staticmethod
    def sort_nodes(nodes: List[NodeInfo], mode: str = NODE_SORT_DEFAULT) -> List[NodeInfo]:
        if mode == NODE_SORT_SNR:
            return sorted(
                nodes,
                key=lambda n: (n.snr is None, -(n.snr if n.snr is not None else -999.0), n.short.lower()),
            )
        if mode == NODE_SORT_DISTANCE:
            return sorted(
                nodes,
                key=lambda n: (
                    n.distance_m is None,
                    n.distance_m if n.distance_m is not None else 1e12,
                    n.short.lower(),
                ),
            )
        return sorted(
            nodes,
            key=lambda n: (
                not n.is_favorite,
                n.distance_m is None,
                n.distance_m if n.distance_m is not None else 1e12,
                n.short.lower(),
            ),
        )

    def _patch_node_favorite(self, iface, num: int, favorite: bool) -> None:
        nodes = getattr(iface, "nodes", None) or {}
        for n in nodes.values():
            nnum = _parse_node_num(n.get("num"))
            if nnum == num:
                n["isFavorite"] = favorite
                return

    def set_node_favorite(self, num: int, favorite: bool) -> Optional[str]:
        iface = self._iface()
        if iface is None:
            return t("radio.not_connected")
        try:
            local = iface.localNode
            if favorite:
                local.setFavorite(num)
            else:
                local.removeFavorite(num)
            self._patch_node_favorite(iface, num, favorite)
            self.log(f"node {num} favorite={favorite}")
            self._notify("state")
            return None
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    def dm_peers(self) -> List[DmPeer]:
        peers: List[DmPeer] = []
        for peer_num, msgs in self.dm_msgs.items():
            if not msgs:
                continue
            last = msgs[-1]
            peers.append(
                DmPeer(
                    peer_num=peer_num,
                    short=self._name_for_num(peer_num),
                    last_ts=last.ts,
                    last_text=last.text[:40],
                )
            )
        peers.sort(key=lambda p: p.last_ts, reverse=True)
        return peers

    def channels_list(self) -> List[ChannelInfo]:
        iface = self._iface()
        if iface is None:
            return []
        try:
            from meshtastic.protobuf import channel_pb2

            local = iface.localNode
            channels = getattr(local, "channels", None) or []
            out: List[ChannelInfo] = []
            for i, ch in enumerate(channels):
                if ch is None:
                    out.append(ChannelInfo(i, "", 0, "OFF"))
                    continue
                role = int(ch.role)
                name = getattr(ch.settings, "name", "") or f"ch{i}"
                out.append(ChannelInfo(i, name, role, ROLE_NAMES.get(role, "?")))
            while len(out) < 8:
                out.append(ChannelInfo(len(out), "", 0, "OFF"))
            return out[:8]
        except Exception as exc:  # noqa: BLE001
            self.log(f"channels_list error: {exc}")
            return [ChannelInfo(i, f"ch{i}", 0, "OFF") for i in range(8)]

    def enabled_channels(self) -> List[ChannelInfo]:
        return [c for c in self.channels_list() if c.role in (1, 2)]

    def channel_name(self, index: int) -> str:
        for c in self.channels_list():
            if c.index == index and c.role != 0:
                return c.name or f"ch{index}"
        return f"ch{index}"

    def write_channel(self, index: int, name: str, role: int, psk_bytes: bytes) -> Optional[str]:
        iface = self._iface()
        if iface is None:
            return t("radio.not_connected")
        try:
            from meshtastic.protobuf import channel_pb2
            from meshtastic.util import fromPSK

            local = iface.localNode
            channels = local.channels
            while len(channels) <= index:
                channels.append(channel_pb2.Channel())
            ch = channels[index]
            ch.index = index
            ch.role = role
            ch.settings.name = name
            if psk_bytes:
                ch.settings.psk = psk_bytes
            else:
                ch.settings.psk = fromPSK("none")
            local.writeChannel(index)
            self.log(f"wrote channel {index} {name} role={role}")
            return None
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    def delete_channel(self, index: int) -> Optional[str]:
        if index == 0:
            return "Primary channel cannot be deleted"
        try:
            from meshtastic.protobuf import channel_pb2

            return self.write_channel(index, "", channel_pb2.Channel.Role.DISABLED, b"")
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    def short_for_num(self, num: int) -> str:
        return self._short_for_num(num)

    def _name_for_num(self, num: int) -> str:
        """Human-readable node name (long name preferred), else a !hex node id."""
        iface = self._iface()
        if iface is not None:
            nodes = getattr(iface, "nodes", None) or {}
            node = nodes.get(num)
            if node:
                user = node.get("user") or {}
                name = user.get("longName") or user.get("shortName")
                if name:
                    return str(name)
        return f"!{num & 0xffffffff:08x}"

    def name_for_num(self, num: int) -> str:
        return self._name_for_num(num)

    @staticmethod
    def _node_row_metrics(n: NodeInfo) -> str:
        extra = []
        if n.distance_m is not None:
            extra.append(format_distance(n.distance_m))
        if n.snr is not None:
            extra.append(f"SNR {n.snr}")
        if n.battery is not None:
            extra.append(f"{n.battery}%")
        return f" ({', '.join(extra)})" if extra else ""

    def node_row_lines(self, n: NodeInfo) -> Tuple[str, str]:
        """Primary line (short + metrics) and secondary line (long name)."""
        prefix = "* " if n.is_favorite else ""
        line1 = f"{prefix}{n.short}{self._node_row_metrics(n)}"
        long_name = (n.long or "").strip()
        if long_name and long_name.casefold() != (n.short or "").strip().casefold():
            line2 = long_name
        else:
            line2 = ""
        return line1, line2

    def format_node_row(self, n: NodeInfo) -> str:
        line1, line2 = self.node_row_lines(n)
        if line2:
            return f"{line1} — {line2}"
        return line1

    @staticmethod
    def filter_nodes(nodes: List[NodeInfo], query: str) -> List[NodeInfo]:
        q = query.strip().lower()
        if not q:
            return list(nodes)
        out: List[NodeInfo] = []
        for n in nodes:
            blob = f"{n.short} {n.long} {n.node_id} {n.num}".lower()
            if q in blob:
                out.append(n)
        return out

    def node_rows(self) -> List[str]:
        return [self.format_node_row(n) for n in self.node_list()]

    def node_rows_for(self, nodes: List[NodeInfo]) -> List[str]:
        return [self.format_node_row(n) for n in nodes]

    def snapshot(self) -> RadioSnapshot:
        with self._lock:
            return RadioSnapshot(
                state=self.state,
                error=self.error,
                devices=list(self.devices),
                my_num=self.my_num,
                my_lat=self.my_lat,
                my_lon=self.my_lon,
                nodes_loading=self.nodes_loading,
                connect_hint=self.connect_hint,
            )
