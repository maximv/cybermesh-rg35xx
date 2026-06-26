"""Tiny runtime i18n: EN (default) / RU, switchable at runtime, persisted to lang.txt."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

LANGUAGES = ("en", "ru")
LANG_NAMES = {"en": "EN", "ru": "RU"}
DEFAULT_LANG = "en"

_lang = DEFAULT_LANG

# key -> {"en": ..., "ru": ...}. Values may contain {named} format fields.
_CATALOG = {
    # --- menu ---
    "menu.send": {"en": "Send", "ru": "Отправить"},
    "menu.messages": {"en": "Messages", "ru": "Сообщения"},
    "menu.nodes": {"en": "Nodes", "ru": "Узлы"},
    "menu.channels": {"en": "Channels", "ru": "Каналы"},
    "menu.map": {"en": "Map", "ru": "Карта"},
    "menu.mynode": {"en": "My node", "ru": "Мой узел"},
    "menu.sound": {"en": "Sound: {state}", "ru": "Звук: {state}"},
    "menu.lang": {"en": "Language: {lang}", "ru": "Язык: {lang}"},
    "menu.rescan": {"en": "Rescan", "ru": "Пересканировать"},
    "menu.disconnect": {"en": "Disconnect", "ru": "Отключить"},
    "menu.quit": {"en": "Quit", "ru": "Выход"},
    "menu.title": {"en": "MENU", "ru": "МЕНЮ"},
    "state.on": {"en": "on", "ru": "вкл"},
    "state.off": {"en": "off", "ru": "выкл"},
    # --- status / toasts ---
    "status.sound_unavailable": {"en": "Sound unavailable", "ru": "Звук недоступен"},
    "status.sound_on": {"en": "Sound on", "ru": "Звук включён"},
    "status.sound_off": {"en": "Sound off", "ru": "Звук выключен"},
    "status.lang_set": {"en": "Language: English", "ru": "Язык: русский"},
    "status.sort": {"en": "Sort: {label}", "ru": "Сортировка: {label}"},
    "status.filter_reset": {"en": "Filter cleared", "ru": "Фильтр сброшен"},
    "status.no_node_data": {"en": "No node data", "ru": "Нет данных об узле"},
    "status.no_gps_nodes": {"en": "No nodes with GPS", "ru": "Нет узлов с GPS"},
    "status.no_node_selected": {"en": "No node selected", "ru": "Узел не выбран"},
    "status.volume": {"en": "Volume {pct}%", "ru": "Громкость {pct}%"},
    "status.volume_na": {"en": "Volume control unavailable", "ru": "Громкость недоступна"},
    "hud.clock": {"en": "{time}", "ru": "{time}"},
    "hud.volume": {"en": "VOL {pct}%", "ru": "ЗВ {pct}%"},
    "hud.battery": {"en": "BAT {pct}%{chg}", "ru": "БАТ {pct}%{chg}"},
    "status.map_theme": {"en": "Map: {theme}", "ru": "Карта: {theme}"},
    # --- send results ---
    "send.sending": {"en": "Sending…", "ru": "Отправка…"},
    "send.error": {"en": "Error: {err}", "ru": "Ошибка: {err}"},
    "send.reply_sent": {"en": "Reply sent", "ru": "Ответ отправлен"},
    "send.sent": {"en": "Sent", "ru": "Отправлено"},
    "send.dm_sent": {"en": "DM sent", "ru": "ЛС отправлено"},
    # --- favorites ---
    "fav.added": {"en": "Favorited", "ru": "В избранном"},
    "fav.removed": {"en": "Unfavorited", "ru": "Убрано из избранного"},
    # --- channels edit ---
    "chan.saved": {"en": "Channel saved", "ru": "Канал сохранён"},
    "chan.deleted": {"en": "Channel deleted", "ru": "Канал удалён"},
    # --- chat ---
    "chat.you": {"en": "You", "ru": "Вы"},
    "chat.reply_to": {"en": "Re {name}", "ru": "В ответ {name}"},
    "chat.no_messages": {"en": "(no messages yet)", "ru": "(пока нет сообщений)"},
    "reply.label": {"en": "reply {sender}", "ru": "ответ {sender}"},
    # --- map themes ---
    "map.theme_light": {"en": "light", "ru": "светлая"},
    "map.theme_dark": {"en": "dark", "ru": "тёмная"},
    # --- map header ---
    "map.title": {"en": "Map z{z}", "ru": "Карта z{z}"},
    "map.tag_light": {"en": "light", "ru": "светл"},
    "map.tag_dark": {"en": "dark", "ru": "тёмн"},
    "map.hdr_count": {"en": "  {n} nodes", "ru": "  {n} узл."},
    "map.hdr_nodes": {"en": "  nodes ({n})", "ru": "  узлы ({n})"},
    "map.hdr_loading": {"en": "  loading", "ru": "  загрузка"},
    # --- default presets ---
    "preset.online": {"en": "Online", "ru": "На связи"},
    # --- context menu ---
    "ctx.reply": {"en": "Reply", "ru": "Ответить"},
    "ctx.dm": {"en": "DM", "ru": "ЛС"},
    "ctx.info": {"en": "Info", "ru": "Инфо"},
    "ctx.cancel": {"en": "Cancel", "ru": "Отмена"},
    "ctx.fav_add": {"en": "Favorite", "ru": "В избранное"},
    "ctx.fav_del": {"en": "Unfavorite", "ru": "Убрать из избранного"},
    "ctx.resend": {"en": "Resend", "ru": "Отправить повторно"},
    "ctx.title": {"en": "Action", "ru": "Действие"},
    # --- scan ---
    "scan.searching_bt": {"en": "Searching Bluetooth", "ru": "Поиск Bluetooth"},
    "scan.error": {"en": "Error:", "ru": "Ошибка:"},
    "scan.none_found": {"en": "No devices found.", "ru": "Устройств не найдено."},
    "scan.none_hint": {
        "en": "Enable BT on Heltec. Select — scan.",
        "ru": "Включите BT на Heltec. Select — скан.",
    },
    "scan.choose": {
        "en": "Choose radio (A — connect):",
        "ru": "Выберите радио (A — подключить):",
    },
    "scan.choose_scanning": {
        "en": "Scanning… (A — connect saved):",
        "ru": "Поиск… (A — подключить сохранённое):",
    },
    # --- DM / messages list ---
    "dm.header": {"en": "DM: {short}", "ru": "ЛС: {short}"},
    "dms.title": {"en": "Messages", "ru": "Сообщения"},
    "dms.empty": {"en": "(no direct messages)", "ru": "(нет личных сообщений)"},
    # --- nodes list ---
    "nodes.title_filtered": {"en": "Nodes {n}/{m}", "ru": "Узлы {n}/{m}"},
    "nodes.title": {"en": "Nodes ({n})", "ru": "Узлы ({n})"},
    "nodes.search": {"en": "Search: {q}", "ru": "Поиск: {q}"},
    "nodes.no_match": {"en": "(no matches)", "ru": "(нет совпадений)"},
    "nodes.loading": {"en": "loading…", "ru": "загрузка…"},
    "nodes.empty": {"en": "(empty)", "ru": "(пусто)"},
    # --- channels view ---
    "chcfg.title": {"en": "Channels", "ru": "Каналы"},
    "chedit.title": {"en": "Channel {idx}", "ru": "Канал {idx}"},
    "chedit.name": {"en": "Name: {val}", "ru": "Имя: {val}"},
    "chedit.role": {"en": "Role: {val}", "ru": "Роль: {val}"},
    "chedit.psk": {"en": "PSK: {val}", "ru": "PSK: {val}"},
    "chedit.psk_none": {"en": "(empty=none)", "ru": "(пусто=none)"},
    "common.empty": {"en": "(empty)", "ru": "(пусто)"},
    # --- node info ---
    "nodeinfo.title": {"en": "Node: {title}", "ru": "Узел: {title}"},
    "nodeinfo.title_default": {"en": "Node info", "ru": "Инфо об узле"},
    # --- generic list ---
    "list.send": {"en": "Send", "ru": "Отправить"},
    # --- keyboard destinations ---
    "kbd.dest_channel": {"en": "channel #{idx}", "ru": "канал #{idx}"},
    "kbd.dest_dm": {"en": "DM {short}", "ru": "ЛС {short}"},
    "kbd.dest_chname": {"en": "channel name", "ru": "имя канала"},
    "kbd.dest_filter": {"en": "node search", "ru": "поиск узла"},
    "kbd.dest_psk": {"en": "PSK", "ru": "PSK"},
    "kbd.full": {"en": "Limit {max} bytes reached", "ru": "Достигнут лимит {max} байт"},
    # --- my node ---
    "mynode.title": {"en": "My node", "ru": "Мой узел"},
    "saved.device": {"en": "saved", "ru": "сохранённое"},
    # --- settings editor ---
    "menu.settings": {"en": "Settings", "ru": "Настройки"},
    "settings.title": {"en": "Settings", "ru": "Настройки"},
    "settings.owner_long": {"en": "owner.long name", "ru": "owner.имя"},
    "settings.owner_short": {"en": "owner.short name", "ru": "owner.метка"},
    "settings.empty": {"en": "(connect to a radio first)", "ru": "(сначала подключитесь к радио)"},
    "settings.saved": {"en": "Saved", "ru": "Сохранено"},
    "settings.no_change": {"en": "No change", "ru": "Без изменений"},
    "settings.write_failed": {"en": "Write failed", "ru": "Ошибка записи"},
    "settings.error": {"en": "Error: {err}", "ru": "Ошибка: {err}"},
    "footer.settings": {
        "en": "↑↓:field ←→:change A:edit/save B:back",
        "ru": "↑↓:поле ←→:менять A:правка/сохр B:назад",
    },
    # --- connect status ---
    "connect.ble": {"en": "Connecting over BLE", "ru": "Подключение по BLE"},
    # --- footers ---
    "footer.menu": {
        "en": "↑↓:item A:select B:close",
        "ru": "↑↓:пункт A:выбор B:закрыть",
    },
    "footer.kbd": {
        "en": "A:type X:space B:Bksp Y:layer L1:Shift Start:OK Select:Cancel",
        "ru": "A:ввод X:проб B:Backsp Y:слой L1:Shift Start:OK Select:Отмена",
    },
    "footer.chat": {
        "en": "↑↓:select L1/L2:scroll L2/R2:chan X:write A:act Y:nodes Start/M:menu",
        "ru": "↑↓:выбор L1/L2:стр L2/R2:канал X:писать A:действ Y:узлы Start/M:меню",
    },
    "footer.dm": {
        "en": "↑↓:select L1/L2:scroll X:write A:reply Y:node B:back",
        "ru": "↑↓:выбор L1/L2:стр X:писать A:ответ Y:узел B:назад",
    },
    "footer.map": {
        "en": "dpad:node stick:pan L1/L2:zoom A:write X:theme Y:me B:back",
        "ru": "крест:узел стик:пан L1/L2:зум A:писать X:тема Y:я B:назад",
    },
    "footer.chcfg": {"en": "A:edit B:back", "ru": "A:редакт B:назад"},
    "footer.chedit": {
        "en": "A:field Start:save Select:del B:back",
        "ru": "A:поле Start:сохр Select:удал B:назад",
    },
    "footer.dms": {"en": "A:open B:back", "ru": "A:открыть B:назад"},
    "footer.nodes": {
        "en": "L2/R2:sort Y:search X:* Select:clear B:back",
        "ru": "L2/R2:сорт Y:поиск X:* Select:сброс B:назад",
    },
    "footer.ctx": {"en": "A:select B:cancel", "ru": "A:выбор B:отмена"},
    "footer.nodeinfo": {"en": "L1/L2:scroll B:back", "ru": "L1/L2:стр B:назад"},
    "footer.default": {
        "en": "A:OK B:Back X:Kbd Y:Nodes Start/M:Menu Select:Scan",
        "ru": "A:OK B:Назад X:Клав Y:Узлы Start/M:Меню Select:Скан",
    },
    # --- node sort labels ---
    "sort.default": {"en": "fav+near", "ru": "избр+рядом"},
    "sort.snr": {"en": "signal", "ru": "сигнал"},
    "sort.distance": {"en": "distance", "ru": "дистанция"},
    # --- position source ---
    "pos.override": {"en": "position.txt file", "ru": "файл position.txt"},
    "pos.manual": {"en": "fixed (MANUAL)", "ru": "фиксированная (MANUAL)"},
    "pos.external": {"en": "external GPS", "ru": "внешний GPS"},
    "pos.internal": {"en": "internal GPS", "ru": "встроенный GPS"},
    "pos.unknown": {"en": "unknown", "ru": "неизвестно"},
    # --- radio ---
    "radio.not_visible": {
        "en": "Heltec not visible ({address}). Power on the node, disable BLE on phone.",
        "ru": "Heltec не виден ({address}). Включите узел, отключите BLE на телефоне.",
    },
    "radio.me": {"en": "Me", "ru": "Я"},
    "radio.ble_node": {"en": "BLE node", "ru": "BLE-узел"},
    "radio.not_connected": {"en": "Not connected", "ru": "Не подключено"},
    "hint.scanning_ble": {"en": "Scanning BLE", "ru": "Сканирование BLE"},
    "hint.connecting": {"en": "Connecting", "ru": "Подключение"},
    "err.not_visible_hint": {
        "en": " — power on Heltec, disable BLE on phone",
        "ru": " — включите Heltec, отключите BLE на телефоне",
    },
    "err.timeout_hint": {
        "en": " — BLE timeout, try again",
        "ru": " — таймаут BLE, попробуйте ещё раз",
    },
    "err.pair_hint": {
        "en": " — run: bluetoothctl pair/trust {address}",
        "ru": " — выполните: bluetoothctl pair/trust {address}",
    },
    "err.ble_generic": {"en": "BLE error", "ru": "Ошибка BLE"},
    # --- node detail lines ---
    "nd.node": {"en": "Node: {short}", "ru": "Узел: {short}"},
    "nd.no_details": {"en": "No details yet", "ru": "Подробности пока нет"},
    "nd.not_found": {"en": "Node not found in NodeDB", "ru": "Узел не найден в NodeDB"},
    "nd.favorite_yes": {"en": "Favorite: yes", "ru": "Избранный: да"},
    "nd.fixed_gps_on": {"en": "Fixed GPS: on", "ru": "Fixed GPS: вкл"},
    "nd.distance": {"en": "Distance: {val}", "ru": "Дистанция: {val}"},
    "nd.lat": {"en": "Lat: {val}", "ru": "Широта: {val}"},
    "nd.lon": {"en": "Lon: {val}", "ru": "Долгота: {val}"},
    "nd.source": {"en": "Source: {val}", "ru": "Источник: {val}"},
    "nd.no_position": {"en": "Position: none", "ru": "Позиция: нет"},
    "nd.battery": {"en": "Battery: {val}%", "ru": "Батарея: {val}%"},
    "nd.heard": {"en": "Heard: {val}", "ru": "Слышали: {val}"},
    "nd.hops": {"en": "Hops: {val}", "ru": "Хопов: {val}"},
    "nd.voltage": {"en": "Voltage: {val} V", "ru": "Напряжение: {val} V"},
    "nd.airtime": {"en": "Airtime: {val}%", "ru": "Загрузка эфира: {val}%"},
    "nd.role": {"en": "Role: {val}", "ru": "Роль: {val}"},
    "nd.my_undefined": {"en": "Own node not identified", "ru": "Свой узел не определён"},
    "nd.connect_ble": {"en": "Connect to a radio over BLE", "ru": "Подключитесь к радио по BLE"},
    "nd.coords_local": {"en": "--- Coordinates (local) ---", "ru": "--- Координаты (локально) ---"},
    "nd.coords_map": {"en": "--- Coordinates (map) ---", "ru": "--- Координаты (карта) ---"},
    "nd.file": {"en": "File: {name}", "ru": "Файл: {name}"},
    "nd.coords_map_none": {"en": "Map coordinates: none", "ru": "Координаты на карте: нет"},
    "nd.misc_nodedb": {"en": "other NodeDB", "ru": "прочее NodeDB"},
}


def normalize_lang(lang: Optional[str]) -> str:
    if lang and lang.lower() in LANGUAGES:
        return lang.lower()
    return DEFAULT_LANG


def set_language(lang: Optional[str]) -> str:
    global _lang
    _lang = normalize_lang(lang)
    return _lang


def get_language() -> str:
    return _lang


def lang_name(lang: Optional[str] = None) -> str:
    return LANG_NAMES.get(normalize_lang(lang or _lang), "EN")


def toggle_language() -> str:
    idx = LANGUAGES.index(_lang) if _lang in LANGUAGES else 0
    return set_language(LANGUAGES[(idx + 1) % len(LANGUAGES)])


def t(key: str, **fmt) -> str:
    entry = _CATALOG.get(key)
    if entry is None:
        return key.format(**fmt) if fmt else key
    text = entry.get(_lang) or entry.get(DEFAULT_LANG) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def load_language(port_dir: Optional[Path]) -> Optional[str]:
    if port_dir is None:
        return None
    path = port_dir / "lang.txt"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError:
        return None
    return normalize_lang(raw) if raw in LANGUAGES else None


def save_language(port_dir: Path, lang: str) -> None:
    try:
        port_dir.mkdir(parents=True, exist_ok=True)
        (port_dir / "lang.txt").write_text(normalize_lang(lang) + "\n", encoding="utf-8")
    except OSError:
        pass
