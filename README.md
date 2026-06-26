# Cybermesh

BLE-клиент Meshtastic для Anbernic RG35xx (640×480, SDL mali).

Репозиторий: [github.com/maximv/cybermesh-rg35xx](https://github.com/maximv/cybermesh-rg35xx)

## Структура на SD-карте

```
/mnt/mmc/Roms/PORTS/
├── Cybermesh.sh          ← пункт меню PORTS
└── Cybermesh/            ← этот репозиторий
    ├── Cybermesh.sh
    ├── cybermesh_mvp/
    ├── assets/
    ├── pylibs/           ← создаётся install_deps.sh на устройстве
    └── scripts/
```

## Разработка (Mac)

```bash
git clone https://github.com/maximv/cybermesh-rg35xx.git
cd cybermesh-rg35xx
python3 smoke_test.py
```

## Anbernic: первая установка

По SSH на консоли:

```bash
/mnt/mmc/Roms/PORTS/Cybermesh/scripts/setup-device.sh \
  https://github.com/maximv/cybermesh-rg35xx.git
```

Скрипт удалит старые `Meshtastic/` / `Cybermesh/`, сделает `git clone`, установит зависимости и положит `Cybermesh.sh` в меню PORTS.

## Anbernic: обновление

```bash
cd /mnt/mmc/Roms/PORTS/Cybermesh
./scripts/update-on-device.sh
```

## Зависимости на устройстве

```bash
./install_deps.sh
```

Требуется `python3` и `pip`. Пакет `meshtastic[ble]` ставится в `pylibs/` (exFAT-safe, без venv).

## CI

На каждый push в GitHub Actions запускается `smoke_test.py`.

Опциональный деплой на Anbernic — см. закомментированный job в `.github/workflows/ci.yml`.
