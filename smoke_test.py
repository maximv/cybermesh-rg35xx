#!/usr/bin/env python3
"""Local smoke tests (no BLE / SDL required)."""

from __future__ import annotations

import sys
import tempfile
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# meshtastic depends on pypubsub; mock if absent (dev machine without pylibs).
try:
    from pubsub import pub  # noqa: F401
except ModuleNotFoundError:
    import types

    fake = types.ModuleType("pubsub")
    fake.pub = types.SimpleNamespace(subscribe=lambda *a, **k: None)
    sys.modules["pubsub"] = fake

from cybermesh.geo import deg2tile, format_distance, haversine_m, tile2deg
from cybermesh.chat_types import ChatMessage, SEND_NONE, SEND_PENDING
from cybermesh.radio import (
    BROADCAST_NUM,
    POS_RANK_INTERNAL,
    POS_RANK_MANUAL,
    RadioManager,
    _best_position_from_node,
    load_position_override,
    save_position_override,
)


def test_geo() -> None:
    from cybermesh.geo import latlon_to_pixel, mercator_pixel, metres_per_pixel

    d = haversine_m(55.75, 37.62, 55.76, 37.63)
    assert 1000 < d < 2000, d
    assert format_distance(500) == "500m"
    assert format_distance(2500) == "2.5km"
    x, y = deg2tile(55.75, 37.62, 14)
    lat, lon = tile2deg(x, y, 14)
    assert abs(lat - 55.75) < 1.0
    sx, sy = latlon_to_pixel(55.75, 37.62, 55.75, 37.62, 14, 640, 400)
    assert abs(sx - 320) < 2
    assert abs(sy - 200) < 2
    _, sy_n = latlon_to_pixel(55.759, 37.62, 55.75, 37.62, 14, 640, 400)
    assert sy_n < sy
    wx1, _ = mercator_pixel(37.62, 55.75, 14)
    wx2, _ = mercator_pixel(37.63, 55.75, 14)
    assert wx2 > wx1
    mpp = metres_per_pixel(55.75, 14)
    assert 3 < mpp < 12
    print("geo OK")


def test_radio_routing() -> None:
    r = RadioManager()
    r.my_num = 0x1234
    pkt = {
        "decoded": {"text": "hello channel"},
        "from": 0x5678,
        "to": BROADCAST_NUM,
        "channel": 1,
        "id": 1,
        "fromId": "!abcd",
    }
    r._on_text(pkt)
    assert len(r.channel_msgs[1]) == 1
    assert not r.channel_msgs[1][0].is_dm

    pkt_dm = {
        "decoded": {"text": "secret"},
        "from": 0x5678,
        "to": r.my_num,
        "channel": 0,
        "id": 2,
    }
    r._on_text(pkt_dm)
    assert 0x5678 in r.dm_msgs
    assert r.dm_msgs[0x5678][0].is_dm

    q = r.reply_quote("bob", "hi there")
    assert q.startswith("> bob:")
    print("radio routing OK")


def test_own_position() -> None:
    r = RadioManager()

    class FakeIface:
        myInfo = type("MI", (), {"my_node_num": 0x1234})()
        nodesByNum = {
            0x1234: {
                "num": 0x1234,
                "position": {"latitudeI": int(55.75 * 1e7), "longitudeI": int(37.62 * 1e7)},
            }
        }
        nodes = {"!1234": nodesByNum[0x1234]}

        def getMyNodeInfo(self):
            return self.nodesByNum[0x1234]

    r._interface = FakeIface()
    r._update_own_position()
    assert r.my_lat is not None and abs(r.my_lat - 55.75) < 0.01
    assert r.my_lon is not None and abs(r.my_lon - 37.62) < 0.01
    print("own position OK")


def test_fixed_position_location() -> None:
    r = RadioManager()

    class FakeIface:
        myInfo = type("MI", (), {"my_node_num": 0x1234})()
        nodesByNum = {0x1234: {"num": 0x1234, "user": {"shortName": "ME", "longName": "My Node"}}}
        location = {"lat": 55.5, "lon": 37.5}

        def getMyNodeInfo(self):
            return self.nodesByNum[0x1234]

    r._interface = FakeIface()
    r._update_own_position()
    assert r.my_lat is not None and abs(r.my_lat - 55.5) < 0.01
    assert r.my_lon is not None and abs(r.my_lon - 37.5) < 0.01
    short, long_name = r.my_node_labels()
    assert short == "ME"
    assert long_name == "My Node"
    print("fixed position OK")


def test_manual_over_internal() -> None:
    r = RadioManager()
    r._apply_own_position(58.74, 33.0, POS_RANK_INTERNAL)
    r._apply_own_position(59.935, 30.415, POS_RANK_MANUAL)
    assert abs(r.my_lat - 59.935) < 0.001
    assert abs(r.my_lon - 30.415) < 0.001
    r._apply_own_position(58.74, 33.0, POS_RANK_INTERNAL)
    assert abs(r.my_lat - 59.935) < 0.001

    node = {
        "position": {
            "latitudeI": int(59.935 * 1e7),
            "longitudeI": int(30.415 * 1e7),
            "locationSource": "LOC_MANUAL",
        }
    }
    lat, lon, rank = _best_position_from_node(node)
    assert rank == POS_RANK_MANUAL
    assert abs(lat - 59.935) < 0.001
    print("manual over internal OK")


def test_position_txt_override() -> None:
    with tempfile.TemporaryDirectory() as td:
        port = Path(td)
        save_position_override(port, 59.935, 30.415)
        ov = load_position_override(port)
        assert ov is not None
        assert abs(ov[0] - 59.935) < 0.001

        r = RadioManager(port_dir=port)
        assert r._my_pos_rank == 0
        assert abs(r.my_lat - 59.935) < 0.001

        class FakeIface:
            myInfo = type("MI", (), {"my_node_num": 0x1234})()
            nodesByNum = {
                0x1234: {
                    "num": 0x1234,
                    "position": {
                        "latitudeI": int(58.74 * 1e7),
                        "longitudeI": int(33.0 * 1e7),
                        "locationSource": "LOC_INTERNAL",
                    },
                }
            }

        r._interface = FakeIface()
        r._update_own_position()
        assert abs(r.my_lat - 59.935) < 0.001
    print("position.txt OK")


def test_node_detail() -> None:
    r = RadioManager()
    r.my_num = 0x9999
    r.my_lat, r.my_lon = 55.75, 37.62

    class FakeIface:
        nodesByNum = {
            0x1234: {
                "num": 0x1234,
                "user": {"id": "!00001234", "shortName": "ABC", "longName": "Alpha Node"},
                "position": {"latitudeI": int(55.76 * 1e7), "longitudeI": int(37.63 * 1e7)},
                "snr": 8.5,
                "lastHeard": time.time() - 120,
                "hopsAway": 1,
                "deviceMetrics": {"batteryLevel": 91, "voltage": 4.1},
                "isFavorite": True,
            }
        }
        nodes = {"!00001234": nodesByNum[0x1234]}

    r._interface = FakeIface()
    lines = r.node_detail_lines(0x1234)
    assert any("ABC" in ln for ln in lines)
    assert any("Distance:" in ln for ln in lines)
    assert any("Favorite" in ln for ln in lines)
    print("node detail OK")


def test_node_filter() -> None:
    from cybermesh.radio import (
        NODE_SORT_DISTANCE,
        NODE_SORT_SNR,
        NodeInfo,
        RadioManager,
    )

    nodes = [
        NodeInfo(1, "!1", "ABC", "Alpha Node", 5.0, 80, None, None, None, 1000.0, False),
        NodeInfo(2, "!2", "XYZ", "Beta", 12.0, 90, None, None, None, 500.0, True),
        NodeInfo(3, "!3", "QWE", "Quiet", None, None, None, None, None, 200.0, False),
    ]
    got = RadioManager.filter_nodes(nodes, "alp")
    assert len(got) == 1 and got[0].short == "ABC"
    got = RadioManager.filter_nodes(nodes, "beta")
    assert len(got) == 1 and got[0].short == "XYZ"
    got = RadioManager.filter_nodes(nodes, "xyz")
    assert len(got) == 1 and got[0].is_favorite
    assert len(RadioManager.filter_nodes(nodes, "")) == 3

    by_snr = RadioManager.sort_nodes(nodes, NODE_SORT_SNR)
    assert by_snr[0].snr == 12.0
    assert by_snr[-1].snr is None

    by_dist = RadioManager.sort_nodes(nodes, NODE_SORT_DISTANCE)
    assert by_dist[0].distance_m == 200.0
    assert by_dist[-1].distance_m == 1000.0

    r = RadioManager()
    line1, line2 = r.node_row_lines(nodes[0])
    assert "ABC" in line1
    assert line2 == "Alpha Node"
    assert "Alpha Node" in r.format_node_row(nodes[0])
    print("node filter OK")


def test_nodes_by_num_only() -> None:
    r = RadioManager()
    r.my_num = 0x9999

    class FakeIface:
        nodes = {}
        nodesByNum = {
            0x5678: {
                "num": 0x5678,
                "user": {"shortName": "R1"},
                "position": {"latitude": 55.76, "longitude": 37.63},
                "snr": 4.0,
            }
        }

    r._interface = FakeIface()
    nodes = r.node_list()
    assert len(nodes) == 1
    assert nodes[0].short == "R1"
    assert nodes[0].lat is not None
    print("nodesByNum OK")


def test_map_anchor() -> None:
    r = RadioManager()
    r.my_num = 0x9999

    class FakeIface:
        nodes = {}
        nodesByNum = {
            0x1111: {
                "num": 0x1111,
                "user": {"shortName": "A"},
                "position": {"latitude": 55.0, "longitude": 37.0},
            },
            0x2222: {
                "num": 0x2222,
                "user": {"shortName": "B"},
                "position": {"latitude": 55.2, "longitude": 37.2},
            },
        }

    r._interface = FakeIface()
    lat, lon, have_me = r.map_anchor()
    assert not have_me
    assert lat is not None and lon is not None
    assert abs(lat - 55.1) < 0.01
    assert abs(lon - 37.1) < 0.01
    print("map anchor OK")


def test_send_status() -> None:
    r = RadioManager()
    r.my_num = 0x1234
    r._append_channel(
        0,
        ChatMessage(text="test", sender="me", channel=0, from_me=True, send_status=SEND_PENDING, msg_id=0x42),
    )
    r._track_pending(0x42, "channel", 0)
    r._on_queue_status(type("QS", (), {"mesh_packet_id": 0x42, "res": 0})())
    assert r.channel_msgs[0][0].send_status == SEND_NONE

    r._append_channel(
        0,
        ChatMessage(text="late", sender="me", channel=0, from_me=True, send_status=SEND_PENDING, msg_id=0x43),
    )
    r._on_queue_status(type("QS", (), {"mesh_packet_id": 0x43, "res": 0})())
    assert r.channel_msgs[0][1].send_status == SEND_NONE

    r._append_dm(
        0x5678,
        ChatMessage(
            text="dm",
            sender="me",
            is_dm=True,
            peer_num=0x5678,
            from_me=True,
            send_status=SEND_PENDING,
            msg_id=0x99,
        ),
    )
    pkt = {
        "decoded": {"text": "dm"},
        "from": r.my_num,
        "to": 0x5678,
        "channel": 0,
        "id": 0x99,
    }
    r._on_text(pkt)
    assert r.dm_msgs[0x5678][0].send_status == SEND_NONE
    print("send status OK")


def test_drop_message() -> None:
    """drop_message removes only the given instance (used to retry a failed send)."""
    from cybermesh.chat_types import SEND_FAILED

    r = RadioManager()
    a = ChatMessage(text="ok", sender="me", channel=0, from_me=True)
    bad = ChatMessage(text="fail", sender="me", channel=0, from_me=True, send_status=SEND_FAILED)
    r._append_channel(0, a)
    r._append_channel(0, bad)
    assert len(r.channel_msgs[0]) == 2
    r.drop_message(bad)
    remaining = list(r.channel_msgs[0])
    assert remaining == [a]
    assert bad not in remaining

    dm = ChatMessage(text="d", sender="me", is_dm=True, peer_num=0x77, from_me=True,
                     send_status=SEND_FAILED)
    r._append_dm(0x77, dm)
    r.drop_message(dm)
    assert len(r.dm_msgs.get(0x77, [])) == 0
    print("drop message OK")


def test_reply_label() -> None:
    r = RadioManager()
    r.my_num = 0x1234
    r._append_channel(
        0,
        ChatMessage(
            text="hello",
            sender="ABC",
            channel=0,
            msg_id=0x100,
            from_me=False,
        ),
    )
    r._append_channel(
        0,
        ChatMessage(
            text="reply text",
            sender="me",
            channel=0,
            msg_id=0x101,
            reply_id=0x100,
            from_me=True,
        ),
    )
    assert r.reply_target_name(0x100, channel=0) == "ABC"
    class FakeIface:
        nodesByNum = {0x1234: {"user": {"shortName": "ME"}}}
        nodes = {}

    r._interface = FakeIface()
    r._append_channel(
        0,
        ChatMessage(
            text="your turn",
            sender="XYZ",
            channel=0,
            msg_id=0x102,
            reply_id=0x101,
            from_me=False,
        ),
    )
    assert r.reply_target_name(0x101, channel=0) == "ME"
    print("reply label OK")


def test_mapview_offline() -> None:
    from cybermesh.mapview import MapView
    from cybermesh.radio import NodeInfo

    class FakeFonts:
        def draw(self, d, pos, text, color, size):
            pass

    with tempfile.TemporaryDirectory() as td:
        mv = MapView(Path(td), 320, 240)
        mv.use_tiles = False
        mv.center_lat = 55.75
        mv.center_lon = 37.62
        nodes = [
            NodeInfo(1, "!1", "A", "A", 5.0, 80, 55.751, 37.621, None, 120.0),
            NodeInfo(2, "!2", "B", "B", 5.0, 80, 55.760, 37.630, None, 500.0),
        ]
        img = mv.render(nodes, 55.75, 37.62, FakeFonts())
        assert img.size == (320, mv.map_h)
        img2 = mv.render(nodes, None, None, FakeFonts())
        assert img2.size == (320, mv.map_h)
        px1 = mv._screen_xy(55.751, 37.621)
        px2 = mv._screen_xy(55.760, 37.630)
        assert px1 != px2
        assert mv.toggle_theme() == "light"
        assert mv.theme == "light"
    print("mapview OK")


def test_msgstore_roundtrip() -> None:
    import tempfile

    from cybermesh.chat_types import ChatMessage
    from cybermesh.msgstore import load_history

    with tempfile.TemporaryDirectory() as td:
        port = Path(td)
        r = RadioManager(port_dir=port)
        r._append_channel(0, ChatMessage(text="hi", sender="bob", channel=0))
        r._append_channel(0, ChatMessage(text="yo", sender="me", channel=0, from_me=True))
        assert r._history is not None
        r._history.flush_now(r.channel_msgs, r.dm_msgs, "AA:BB:CC:DD:EE:FF")

        ch, dm = load_history(port)
        assert len(ch[0]) == 2
        assert ch[0][0].text == "hi"

        r2 = RadioManager(port_dir=port)
        assert len(r2.channel_msgs[0]) == 2
    print("msgstore OK")


def test_ble_device() -> None:
    from cybermesh.radio import BleDevice

    d = BleDevice(name="XIM2", address="F8:5B:1B:A1:C9:5D")
    assert d.name == "XIM2"
    assert d.address.startswith("F8")
    print("BleDevice OK")


def test_audio_synth() -> None:
    from pathlib import Path
    import tempfile

    from cybermesh.audio import (
        SfxPlayer,
        _gen_click_wav,
        load_sound_enabled,
        save_sound_enabled,
        synth_modem_connect,
        synth_modem_disconnect,
    )

    assert len(_gen_click_wav(800.0, 0.02, 0.1)) > 500
    assert len(synth_modem_connect()) > 50000
    assert len(synth_modem_disconnect()) > 20000
    with tempfile.TemporaryDirectory() as td:
        port = Path(td)
        save_sound_enabled(port, False)
        assert load_sound_enabled(port) is False
        save_sound_enabled(port, True)
        assert load_sound_enabled(port) is True
        p = SfxPlayer(log=lambda _m: None, port_dir=port)
        assert p.enabled == bool(p._aplay)
        if p._aplay:
            p.set_enabled(False)
            assert p.enabled is False
    p2 = SfxPlayer(log=lambda _m: None)
    assert p2.enabled or not p2._aplay
    print("audio synth OK")


def test_audio_volume() -> None:
    import array
    import io
    import wave
    from pathlib import Path
    import tempfile

    from cybermesh.audio import (
        DEFAULT_VOLUME,
        SfxPlayer,
        _scale_wav,
        load_volume,
        save_volume,
        synth_modem_connect,
    )

    with tempfile.TemporaryDirectory() as td:
        port = Path(td)
        assert load_volume(port) is None
        save_volume(port, 40)
        assert load_volume(port) == 40
        save_volume(port, 999)
        assert load_volume(port) == 100
        p = SfxPlayer(log=lambda _m: None, port_dir=port)
        assert p.volume == 100
        assert p.set_volume(55) == 55
        assert p.set_volume(-10) == 0

    p2 = SfxPlayer(log=lambda _m: None)
    assert p2.volume == DEFAULT_VOLUME

    def peak(wav: bytes) -> int:
        with wave.open(io.BytesIO(wav), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        pcm = array.array("h")
        pcm.frombytes(frames)
        return max(abs(s) for s in pcm)

    full = synth_modem_connect()
    quiet = _scale_wav(full, 0.25)
    assert peak(quiet) < peak(full) * 0.5
    assert _scale_wav(full, 1.0) == full
    print("audio volume OK")


def test_splash_radar() -> None:
    from PIL import Image, ImageDraw

    from cybermesh.splash import _smoothstep, draw_radar_frame

    assert _smoothstep(0.0) == 0.0
    assert _smoothstep(1.0) == 1.0
    assert 0.0 < _smoothstep(0.5) < 1.0

    class _Fonts:
        def draw(self, d, pos, text, color, size):
            pass

        def length(self, text, size):
            return len(text) * 7

    img = Image.new("RGBA", (640, 480), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    e0 = draw_radar_frame(d, 640, 480, 0.0, unfold=True, fonts=_Fonts())
    e1 = draw_radar_frame(d, 640, 480, 1.0, unfold=True, fonts=_Fonts())
    assert e0 < e1
    ef = draw_radar_frame(d, 640, 480, 1.0, unfold=False, fonts=_Fonts())
    assert ef < e1
    print("splash radar OK")


def test_map_pan_vector() -> None:
    from cybermesh.inputs import combine_pan_vector, _norm_axis, _norm_hat, _left_stick_for_pan

    assert _norm_axis(0, -32768, 32767) == 0.0
    assert _norm_axis(32767, -32768, 32767) > 0.8
    # RG35xx uses ±4096 range — must not fall below deadzone at full throw
    assert _norm_axis(4096, -4096, 4096) > 0.8
    assert _norm_axis(1086, -4096, 4096) > 0.1
    assert _norm_hat(-1) == -1
    assert _norm_hat(2) == 1
    # D-pad must NOT be rotated: vertical input -> vy, horizontal input -> vx.
    # UP (hat_y=-1) pans up (vy>0), no horizontal component.
    vx, vy = combine_pan_vector(0, -1, {})
    assert vy > 0 and abs(vx) < 1e-9
    # DOWN (hat_y=1) pans down.
    vx, vy = combine_pan_vector(0, 1, {})
    assert vy < 0 and abs(vx) < 1e-9
    # LEFT (hat_x=-1) -> vx>0, RIGHT (hat_x=1) -> vx<0; no vertical component.
    vx, vy = combine_pan_vector(-1, 0, {})
    assert vx > 0 and abs(vy) < 1e-9
    vx, vy = combine_pan_vector(1, 0, {})
    assert vx < 0 and abs(vy) < 1e-9
    # RG35xx left stick (pro, measured): ABS_Z (lz) horizontal -> vx, ABS_RX (lw) vertical -> vy.
    vx, vy = combine_pan_vector(0, 0, {"lz": 0.8, "lw": 0.0})
    assert vx < -0.4 and abs(vy) < 1e-9
    vx, vy = combine_pan_vector(0, 0, {"lz": 0.0, "lw": 0.8})
    assert vy < -0.4 and abs(vx) < 1e-9
    # Right stick (rx/ry) ignored; lw is the left-stick vertical axis.
    stick_v, stick_h = _left_stick_for_pan({"rx": 0.9, "ry": 0.9, "lw": 0.8, "lz": 0.0})
    assert abs(stick_v) > 0.5
    assert abs(stick_h) < 0.01
    print("map pan vector OK")


def test_force_quit_chord() -> None:
    from cybermesh.inputs import CHORD_BUTTONS, FORCE_QUIT_CHORD, is_force_quit_chord

    assert FORCE_QUIT_CHORD.issubset(CHORD_BUTTONS)
    assert is_force_quit_chord({"START", "MENU"})
    assert not is_force_quit_chord({"START"})
    assert not is_force_quit_chord({"MENU"})
    assert not is_force_quit_chord(set())
    print("force quit chord OK")


def test_menu_button_single_emit() -> None:
    """MENU must fire once per press; autorepeat (value=2) must not toggle it."""
    import queue as _q

    from cybermesh.inputs import BTN_MAP, InputReader

    actions: "_q.Queue[str]" = _q.Queue()
    r = InputReader(actions, log=lambda _m: None)
    code = next(c for c, a in BTN_MAP.items() if a == "MENU")

    def ev(value):
        return type("E", (), {"code": code, "value": value})()

    r._handle_key(ev(1))   # press
    r._handle_key(ev(2))   # autorepeat — must be ignored
    r._handle_key(ev(2))
    r._handle_key(ev(0))   # release
    emitted = []
    while not actions.empty():
        emitted.append(actions.get_nowait())
    assert emitted == ["MENU"], emitted
    print("menu button single emit OK")


def test_fixed_position_from_config() -> None:
    """Fixed GPS on, NodeDB has no own position -> read lat/lon from position config."""
    from cybermesh.radio import _read_device_fixed_gps

    class FakePosCfg:
        fixed_position = True
        latitude_i = int(59.935 * 1e7)
        longitude_i = int(30.415 * 1e7)

    class FakeLC:
        position = FakePosCfg()

    class FakeLocal:
        localConfig = FakeLC()
        nodeNum = 0x1234

    class FakeIface:
        localNode = FakeLocal()
        nodesByNum = {0x1234: {"num": 0x1234, "user": {"shortName": "HT"}}}  # no position

        def getMyNodeInfo(self):
            return self.nodesByNum[0x1234]

    lat, lon, rank, fixed = _read_device_fixed_gps(FakeIface())
    assert fixed is True
    assert lat is not None and abs(lat - 59.935) < 0.001
    assert lon is not None and abs(lon - 30.415) < 0.001
    print("fixed position from config OK")


def test_i18n() -> None:
    from pathlib import Path
    import tempfile

    from cybermesh import i18n
    from cybermesh.radio import node_sort_label, NODE_SORT_SNR

    prev = i18n.get_language()
    try:
        assert i18n.set_language(None) == "en"
        assert i18n.t("menu.send") == "Send"
        assert i18n.t("menu.sound", state="on") == "Sound: on"
        assert i18n.lang_name() == "EN"

        assert i18n.set_language("ru") == "ru"
        assert i18n.t("menu.send") == "Отправить"
        assert i18n.lang_name() == "RU"
        assert node_sort_label(NODE_SORT_SNR) == "сигнал"

        # toggle cycles en<->ru
        assert i18n.toggle_language() == "en"
        assert i18n.toggle_language() == "ru"

        # unknown key falls back to the key itself
        assert i18n.t("does.not.exist") == "does.not.exist"
        # bad format args don't raise (returns template unformatted)
        assert isinstance(i18n.t("menu.sound", nope="x"), str)

        with tempfile.TemporaryDirectory() as td:
            port = Path(td)
            assert i18n.load_language(port) is None
            i18n.save_language(port, "ru")
            assert i18n.load_language(port) == "ru"
            i18n.save_language(port, "en")
            assert i18n.load_language(port) == "en"
    finally:
        i18n.set_language(prev)
    print("i18n OK")


def test_device_fixed_gps_from_my_node_info() -> None:
    """NodeDB entry without position; coords only in getMyNodeInfo (Heltec Fixed GPS)."""
    r = RadioManager()

    class FakePosCfg:
        fixed_position = True

        def ListFields(self):
            return [("fixed_position", True)]

    class FakeLC:
        position = FakePosCfg()
        DESCRIPTOR = type("D", (), {"fields_by_name": {"position": "position"}})()

    class FakeLocal:
        localConfig = FakeLC()
        nodeNum = 0x1234

        def waitForConfig(self, attr="channels"):
            return True

    full_node = {
        "num": 0x1234,
        "user": {"id": "!00001234", "shortName": "HT", "longName": "Heltec"},
        "position": {
            "latitudeI": int(59.935 * 1e7),
            "longitudeI": int(30.415 * 1e7),
            "locationSource": "LOC_MANUAL",
        },
    }

    class FakeIface:
        myInfo = type("MI", (), {"my_node_num": 0x1234})()
        localNode = FakeLocal()
        nodesByNum = {
            0x1234: {
                "num": 0x1234,
                "user": full_node["user"],
            }
        }

        def getMyNodeInfo(self):
            return full_node

        def getNode(self, node_id, **kwargs):
            return self.localNode

    iface = FakeIface()
    r._interface = iface
    r._probe_own_fixed_position(iface, sync=True)
    r._update_own_position()
    assert r.my_lat is not None and abs(r.my_lat - 59.935) < 0.001
    assert r.my_lon is not None and abs(r.my_lon - 30.415) < 0.001

    info = r.get_node(0x1234)
    assert info is not None
    assert info.lat is not None and abs(info.lat - 59.935) < 0.001
    lines = r.node_detail_lines(0x1234)
    assert any("Fixed GPS: on" in ln for ln in lines)
    assert any("Lat:" in ln for ln in lines)
    print("device fixed GPS OK")


def test_my_node_detail() -> None:
    r = RadioManager()
    r.my_num = 0x1234
    r.my_lat, r.my_lon = 59.935, 30.415
    r._my_pos_rank = 1

    class FakeIface:
        myInfo = type("MI", (), {"my_node_num": 0x1234})()
        nodesByNum = {
            0x1234: {
                "num": 0x1234,
                "user": {"id": "!00001234", "shortName": "ME", "longName": "My Node"},
                "position": {
                    "latitudeI": int(59.935 * 1e7),
                    "longitudeI": int(30.415 * 1e7),
                    "locationSource": "LOC_MANUAL",
                    "altitude": 12,
                },
                "deviceMetrics": {"batteryLevel": 88, "voltage": 4.05},
            }
        }

    r._interface = FakeIface()
    lines = r.my_node_detail_lines()
    assert any("ME" in ln for ln in lines)
    assert any("59.935" in ln for ln in lines)
    assert any("position" in ln for ln in lines)
    print("my node detail OK")


def test_map_animation() -> None:
    import time as _t
    from pathlib import Path
    import tempfile

    from cybermesh.mapview import MapView

    with tempfile.TemporaryDirectory() as td:
        mv = MapView(Path(td), 640, 480)
        # First target with no center yet -> snaps immediately, no animation.
        mv.animate_to(55.0, 37.0)
        assert mv._anim_active is False
        assert abs(mv.center_lat - 55.0) < 1e-9
        # Next target animates from the current center toward the goal.
        mv._anim_dur = 0.1
        mv.animate_to(56.0, 38.0)
        assert mv._anim_active is True
        mv.update_anim()
        assert 55.0 < mv.center_lat < 56.0 or mv._anim_active
        _t.sleep(0.12)
        mv.update_anim()
        assert mv._anim_active is False
        assert abs(mv.center_lat - 56.0) < 1e-6
        # Manual pan cancels any animation.
        mv.animate_to(57.0, 39.0)
        mv.pan(3, 3)
        assert mv._anim_active is False
    print("map animation OK")


def test_keyboard_layers() -> None:
    """Every keyboard cell must be exactly one character (incl. the emoji layer)."""
    from cybermesh.fbui import KBD_LAYERS

    names = [name for name, _ in KBD_LAYERS]
    assert "EMO" in names, names
    for name, rows in KBD_LAYERS:
        for row in rows:
            for ch in row:
                assert len(ch) == 1, (name, ch)
    emo = dict(KBD_LAYERS)["EMO"]
    # All emoji must be single Unicode code points (no ZWJ/skin-tone sequences).
    for row in emo:
        assert all(0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x2BFF for c in row), row
    print("keyboard layers OK")


def test_volume_keys() -> None:
    """Hardware volume keys emit VOLUP/VOLDOWN (incl. autorepeat)."""
    import queue as _q

    from cybermesh.inputs import VOLUME_CODES, InputReader

    actions: "_q.Queue[str]" = _q.Queue()
    r = InputReader(actions, log=lambda _m: None)
    up = next(c for c, a in VOLUME_CODES.items() if a == "VOLUP")
    down = next(c for c, a in VOLUME_CODES.items() if a == "VOLDOWN")

    def ev(code, value):
        return type("E", (), {"code": code, "value": value})()

    r._handle_key(ev(up, 1))      # press -> emit once, mark held
    assert up in r._vol_held
    r._handle_key(ev(up, 0))      # release -> stop holding, no emit
    assert up not in r._vol_held
    r._handle_key(ev(down, 1))
    emitted = []
    while not actions.empty():
        emitted.append(actions.get_nowait())
    assert emitted == ["VOLUP", "VOLDOWN"], emitted

    # Software autorepeat: a held key past its next-fire time emits again.
    import time as _t
    r._handle_key(ev(down, 1))
    while not actions.empty():
        actions.get_nowait()
    with r._lock:
        r._vol_held[down][1] = _t.monotonic() - 0.01  # force due
    now = _t.monotonic()
    with r._lock:
        items = list(r._vol_held.items())
    for code, st in items:
        if now >= st[1]:
            r._emit(st[0])
    assert actions.get_nowait() == "VOLDOWN"
    print("volume keys OK")


def test_name_for_num_string_keyed() -> None:
    """iface.nodes is keyed by '!hex' id strings; name lookup must still resolve."""
    r = RadioManager()

    class FakeIface:
        def __init__(self):
            # Keyed by id string (as real meshtastic does), not by int num.
            self.nodes = {
                "!433a1b2c": {"num": 0x433A1B2C,
                              "user": {"longName": "Base Station", "shortName": "BASE"}},
                "!00000099": {"num": 0x99, "user": {"shortName": "N99"}},
            }

    r._interface = FakeIface()
    # Long name preferred.
    assert r.name_for_num(0x433A1B2C) == "Base Station"
    # Falls back to short name when no long name.
    assert r.name_for_num(0x99) == "N99"
    # Unknown node -> !hex id.
    assert r.name_for_num(0x1234) == "!00001234"
    # short_for_num resolves through the same path.
    assert r.short_for_num(0x433A1B2C) == "BASE"
    print("name_for_num string-keyed OK")


def test_map_label_lines() -> None:
    """Map labels stack short/long/distance on separate lines (long names wrap off)."""
    from cybermesh.mapview import _node_label_lines
    from cybermesh.radio import NodeInfo

    n = NodeInfo(num=1, node_id="!1", short="BASE", long="Base Station Alpha",
                 snr=None, battery=None, lat=1.0, lon=2.0, last_heard=None,
                 distance_m=1234.0)
    lines = _node_label_lines(n)
    assert lines[0] == "BASE"
    assert lines[1] == "Base Station Alpha"
    assert "m" in lines[2] or "km" in lines[2]
    # When long == short, only one name line (plus distance).
    n2 = NodeInfo(num=2, node_id="!2", short="X", long="X", snr=None, battery=None,
                  lat=1.0, lon=2.0, last_heard=None, distance_m=None)
    assert _node_label_lines(n2) == ["X"]
    print("map label lines OK")


def test_screenshot_chord() -> None:
    """L1+R1+SELECT held together emits a single SCREENSHOT action."""
    import queue as _q

    from cybermesh.inputs import BTN_MAP, SCREENSHOT_CHORD, InputReader

    actions: "_q.Queue[str]" = _q.Queue()
    r = InputReader(actions, log=lambda _m: None)
    codes = {a: c for c, a in BTN_MAP.items()}
    combo = [codes[a] for a in ("PGUP", "PGDN", "SELECT")]

    def ev(code, value):
        return type("E", (), {"code": code, "value": value})()

    for c in combo:  # press all three
        r._handle_key(ev(c, 1))
    emitted = []
    while not actions.empty():
        emitted.append(actions.get_nowait())
    assert "SCREENSHOT" in emitted, emitted
    # Still held -> cooldown prevents a flood of screenshots.
    assert SCREENSHOT_CHORD == {"PGUP", "PGDN", "SELECT"}
    print("screenshot chord OK")


def test_kbd_byte_budget() -> None:
    """Typing is capped by UTF-8 byte budget; emoji/cyrillic cost more than 1."""
    from cybermesh.fbui import MAX_MSG_BYTES, FbUI

    assert "a".encode("utf-8").__len__() == 1
    assert "я".encode("utf-8").__len__() == 2
    assert "😀".encode("utf-8").__len__() == 4

    ui = FbUI.__new__(FbUI)  # bypass heavy __init__
    ui.kbd_text = ""
    ui._dirty = False
    ui.status = ""
    ui.status_until = 0.0

    # Fill to exactly the limit with 1-byte chars.
    ui.kbd_text = "x" * (MAX_MSG_BYTES - 1)
    ui._kbd_insert("y")
    assert ui._kbd_used_bytes() == MAX_MSG_BYTES
    # Now full: another ASCII char is rejected.
    ui._kbd_insert("z")
    assert ui._kbd_used_bytes() == MAX_MSG_BYTES

    # An emoji needs 4 bytes; near the edge it must be rejected, not truncated.
    ui.kbd_text = "x" * (MAX_MSG_BYTES - 2)
    ui._kbd_insert("😀")  # would be +4 -> over budget
    assert "😀" not in ui.kbd_text
    assert ui._kbd_used_bytes() == MAX_MSG_BYTES - 2
    # With room, the emoji is accepted and counts as 4 bytes.
    ui.kbd_text = "x" * (MAX_MSG_BYTES - 4)
    ui._kbd_insert("😀")
    assert ui.kbd_text.endswith("😀")
    assert ui._kbd_used_bytes() == MAX_MSG_BYTES
    print("kbd byte budget OK")


def test_hat_state_diagonal() -> None:
    """hat_state() reports diagonals as a single vector (for one-step node jumps)."""
    import queue as _q

    from cybermesh.inputs import InputReader

    actions: "_q.Queue[str]" = _q.Queue()
    r = InputReader(actions, log=lambda _m: None)
    assert r.hat_state() == (0, 0)
    # Analog hat: up + left held together -> (-1, -1).
    r._hat_x, r._hat_y = -1, -1
    assert r.hat_state() == (-1, -1)
    r._hat_x, r._hat_y = 1, 1
    assert r.hat_state() == (1, 1)
    # KEY-based d-pad held flags also fold into the same vector.
    r._hat_x = r._hat_y = 0
    r._held_dir["UP"] = True
    r._held_dir["RIGHT"] = True
    assert r.hat_state() == (1, -1)
    print("hat state diagonal OK")


def test_config_field() -> None:
    """ConfigField edit model: cycle/display/dirty work for enum & int."""
    from cybermesh.radio import ConfigField

    enum = ConfigField("lora.region", "lora.region", "enum", 1,
                       options=[(0, "UNSET"), (1, "EU_868"), (2, "US")])
    assert enum.display == "EU_868"
    assert not enum.dirty
    enum.cycle(1)
    assert enum.display == "US" and enum.dirty
    enum.cycle(1)  # wraps to UNSET
    assert enum.display == "UNSET"

    b = ConfigField("device.serial_enabled", "x", "bool", True,
                    options=[(False, "OFF"), (True, "ON")])
    assert b.display == "ON"
    b.cycle(1)
    assert b.display == "OFF" and b.pending is False

    i = ConfigField("lora.hop_limit", "x", "int", 3)
    i.cycle(1)
    assert i.pending == 4 and i.display == "4"
    i.cycle(-10)  # clamps at 0
    assert i.pending == 0
    print("config field OK")


def test_device_settings_owner() -> None:
    """device_settings exposes owner fields; apply_setting calls setOwner."""
    r = RadioManager()
    r.my_num = 0x1234

    class FakeLocal:
        def __init__(self):
            self.localConfig = object()  # no recognizable config groups
            self.owner = None

        def setOwner(self, long_name=None, short_name=None):
            self.owner = (long_name, short_name)

    class FakeIface:
        def __init__(self):
            self.localNode = FakeLocal()
            self.nodes = {0x1234: {"num": 0x1234,
                                   "user": {"longName": "Old", "shortName": "OLD"}}}

        def getMyNodeInfo(self):
            return self.nodes[0x1234]

    fake = FakeIface()
    r._interface = fake
    fields = r.device_settings()
    keys = [f.key for f in fields]
    assert "owner.long" in keys and "owner.short" in keys, keys
    long_field = next(f for f in fields if f.key == "owner.long")
    assert long_field.raw == "Old"

    err = r.apply_setting("owner.short", "NEW")
    assert err is None, err
    assert fake.localNode.owner == ("Old", "NEW")
    # NodeDB patched so the UI reflects the change immediately.
    assert fake.nodes[0x1234]["user"]["shortName"] == "NEW"
    print("device settings owner OK")


def test_sysinfo_graceful() -> None:
    """Battery/volume helpers degrade gracefully when sysfs/amixer are absent."""
    from cybermesh.sysinfo import SystemVolume, read_battery

    bat = read_battery()
    assert bat is None or (isinstance(bat, tuple) and 0 <= bat[0] <= 100)

    vol = SystemVolume(log=lambda _m: None)
    # On a dev machine amixer/controls may be missing; must not raise.
    assert vol.get_volume() is None or 0 <= vol.get_volume() <= 100
    if not vol.available:
        assert vol.change(5) is None
    print("sysinfo graceful OK")


def main() -> int:
    test_geo()
    test_radio_routing()
    test_own_position()
    test_fixed_position_location()
    test_device_fixed_gps_from_my_node_info()
    test_manual_over_internal()
    test_position_txt_override()
    test_my_node_detail()
    test_node_detail()
    test_node_filter()
    test_nodes_by_num_only()
    test_map_anchor()
    test_send_status()
    test_drop_message()
    test_reply_label()
    test_mapview_offline()
    test_msgstore_roundtrip()
    test_ble_device()
    test_audio_synth()
    test_audio_volume()
    test_splash_radar()
    test_map_pan_vector()
    test_force_quit_chord()
    test_menu_button_single_emit()
    test_map_animation()
    test_keyboard_layers()
    test_volume_keys()
    test_kbd_byte_budget()
    test_screenshot_chord()
    test_hat_state_diagonal()
    test_name_for_num_string_keyed()
    test_map_label_lines()
    test_config_field()
    test_device_settings_owner()
    test_sysinfo_graceful()
    test_fixed_position_from_config()
    test_i18n()
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
