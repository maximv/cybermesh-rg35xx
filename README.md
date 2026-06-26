# Cybermesh

BLE-клиент Meshtastic для Anbernic RG35xx (640×480, SDL mali).

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

## GitLab: создать репозиторий и запушить (Mac)

```bash
cd Cybermesh-rg35xx

# Авторизация (один раз) — gitlab.com или свой GitLab:
glab auth login
# или для gitlab.art.su:
# glab auth login --hostname gitlab.art.su

# Создать проект и запушить
glab repo create --private --description "Cybermesh for RG35xx"
git push -u origin main
```

Если ветка называется `master`, замените `main` на `master`.

## Anbernic: первая установка

По SSH на консоли:

```bash
cd /mnt/mmc/Roms/PORTS
curl -O https://…/setup-device.sh   # или скопируйте scripts/setup-device.sh
# проще — после git clone вручную:
git clone git@gitlab.art.su:USER/cybermesh-rg35xx.git Cybermesh
cd Cybermesh
chmod +x scripts/*.sh Cybermesh.sh install_deps.sh
./scripts/setup-device.sh   # если уже в каталоге — достаточно:
./install_deps.sh
./scripts/update-on-device.sh
```

Или одной командой с Mac (если есть SSH):

```bash
ssh root@ANBERNIC_IP 'bash -s' < scripts/setup-device.sh -- \
  git@gitlab.art.su:USER/cybermesh-rg35xx.git
```

## Anbernic: обновление

```bash
cd /mnt/mmc/Roms/PORTS/Cybermesh
./scripts/update-on-device.sh
```

## Локальная разработка

```bash
python3 smoke_test.py
```

## Зависимости на устройстве

```bash
./install_deps.sh
```

Требуется `python3` и `pip`. Пакет `meshtastic[ble]` ставится в `pylibs/` (exFAT-safe, без venv).

## CI/CD

На каждый push в GitLab запускается `smoke_test.py`.

Опциональный деплой на Anbernic через GitLab CI — см. закомментированный job в `.gitlab-ci.yml` (нужны переменные `ANBERNIC_HOST`, `ANBERNIC_USER`, `SSH_PRIVATE_KEY`).
