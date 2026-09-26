# Установка на сервер: пошагово под root

[English](../INSTALL.md) | **Русский**

Инструкция для чистого сервера **Ubuntu 24.04 LTS (noble)**, на котором вы
работаете под `root`. Ubuntu 26.04 пока не поддерживается: `bootstrap.sh` и
preflight откажутся запускаться на любой версии, кроме 24.04.

Схема работы — Ansible с локальным контроллером: всё запускается на самом
сервере, отдельная машина с Ansible не нужна.

## 0. Что подготовить заранее

**Сервер**

- чистая Ubuntu 24.04 LTS, доступ под `root` (или `sudo -i`);
- минимум 2 vCPU и 2 GiB RAM (рекомендуется 4 GiB и больше), не меньше
  10 GiB свободного места на `/`;
- публичный IPv4: назначенный прямо на сервер или через NAT с пробросом портов.

**DNS — сделать до запуска установки.** Создайте у DNS-провайдера шесть
A-записей на публичный IPv4 сервера (префиксы можно поменять во время установки,
ниже — значения по умолчанию для `example.com`). Без них установка не начнётся:
preflight проверяет каждую запись, а сертификаты Let's Encrypt выпускаются
только для имён, которые уже указывают на сервер. Новые записи расходятся от
нескольких минут до пары часов, поэтому заведите их заранее.

| Имя | Назначение |
| --- | --- |
| `matrix.example.com` | Synapse (это же Matrix server name: `@user:matrix.example.com`) |
| `element.example.com` | Element Web |
| `synad.example.com` | Ketesa (админка Synapse) |
| `call.example.com` | Element Call |
| `rtc.example.com` | LiveKit / MatrixRTC |
| `turn.example.com` | классический TURN (Coturn) |

У каждого имени должна быть ровно одна A-запись — на IP этого сервера.
**AAAA-записей быть не должно**: IPv6-режим не поддерживается, и preflight
остановит установку, если найдёт AAAA.

**Порты.** Если перед сервером есть облачный firewall или NAT, откройте или
пробросьте входящие порты (UFW на самом сервере установка настроит сама):

| Порт | Назначение |
| --- | --- |
| TCP 22 (или ваш SSH-порт) | SSH |
| TCP 80, 443 | HTTP/ACME, HTTPS |
| TCP 8448 | федерация (если включаете) |
| TCP+UDP 3478, 5349 | Coturn |
| UDP 57000-57999 | relay Coturn |
| TCP 7881, UDP 62000-62999 | LiveKit RTC |
| UDP 3480, TCP 5449 | встроенный TURN LiveKit |
| UDP 63000-63999 | relay встроенного TURN LiveKit |

Ещё понадобится email для Let's Encrypt и пароль для будущего администратора
Matrix (`@admin`).

## 1. Подключиться к серверу

```bash
ssh root@<IP-сервера>
```

Установку лучше запускать внутри `tmux`, чтобы обрыв SSH не прервал Ansible:

```bash
apt-get update && apt-get install -y tmux
tmux new -s matrix
```

После обрыва связи вернуться в сессию: `tmux attach -t matrix`.

## 2. Обновить систему

```bash
apt-get update
apt-get -y full-upgrade
[ -f /var/run/reboot-required ] && reboot
```

Если сервер ушёл в перезагрузку, подключитесь заново (и снова откройте `tmux`).

## 3. Установить git

```bash
apt-get install -y git ca-certificates
```

**Ansible вручную ставить не нужно, и не стоит.** `bootstrap.sh` сам ставит
Python-зависимости, `skopeo`, `dnsutils` и прочее, создаёт виртуальное окружение
`.venv` и устанавливает в него зафиксированные ansible-core 2.21.4 и коллекции
из `ansible/requirements.yml`. Пакет `ansible` из apt Ubuntu 24.04 слишком старый
и установкой не используется.

## 4. Склонировать репозиторий

Клонируйте в `/opt/matrix-deploy/source` — это стандартное место, с которым
работает CLI `matrix-deploy`:

```bash
mkdir -p /opt/matrix-deploy
git clone https://github.com/guestikchatgpt/matrix_deploy.git /opt/matrix-deploy/source
cd /opt/matrix-deploy/source
git log --oneline -1   # убедиться, что это main и нужный коммит
```

Чтобы поставить конкретный релиз, а не текущий `main`, после клона выполните
`git -C /opt/matrix-deploy/source checkout v1.0.0`. Вместо шагов 3–4 и 6 можно
поставить релиз одной командой — см. [`INSTALLER.md`](INSTALLER.md).

## 5. Проверить DNS до запуска

```bash
for h in matrix element synad call rtc turn; do
  printf '%-8s A=%s AAAA=%s\n' "$h" \
    "$(dig +short A $h.example.com | tr '\n' ' ')" \
    "$(dig +short AAAA $h.example.com | tr '\n' ' ')"
done
```

(`dig` появится после шага 6; до этого можно поставить его вручную:
`apt-get install -y dnsutils`.) Везде должен быть только ваш IPv4, а AAAA —
пустой.

## 6. Запустить установку

```bash
cd /opt/matrix-deploy/source
./bootstrap.sh --install
```

Что происходит:

1. `--install` ставит CLI `/usr/local/sbin/matrix-deploy` и записывает текущий
   коммит в `/opt/matrix-deploy/installed-release`. Репозиторий остаётся на
   месте, копирования нет, потому что он уже лежит в `/opt/matrix-deploy/source`.
2. `bootstrap.sh` ставит системные зависимости, `.venv`, ansible-core и коллекции.
3. Запускается интерактивный `deploy.sh`.

Вопросы `deploy.sh` (установщик задаёт их на английском; в скобках — значение по умолчанию, Enter его принимает):

| Вопрос | Что ответить |
| --- | --- |
| Base domain | ваш домен, например `example.com` |
| Matrix/Element/Ketesa/Call/RTC/TURN prefix | Enter, если DNS сделан по таблице выше |
| Let's Encrypt email | рабочий email |
| Enable Matrix federation? | `y`: общение с другими Matrix-серверами; `n`: закрытый сервер |
| Use this IPv4? | проверить, что определился именно публичный IP сервера |
| Local relay IP for Coturn (только при NAT) | внутренний IP сервера, на который NAT пробрасывает порты |
| Matrix admin password | пароль для `@admin:matrix.<домен>` (не отображается) |

Затем установщик:

- сохраняет топологию в `/etc/matrix-deploy/deployment.yml`;
- запускает preflight: ОС, ресурсы, DNS A/AAAA, свободные порты, apt, выбор
  актуальных stable-версий с проверкой образов в registry;
- фиксирует версии в `/etc/matrix-deploy/versions.yml`;
- показывает план и спрашивает **«Preflight succeeded. Start the deployment?»**.
  По умолчанию ответ «нет», для продолжения введите `y`.

Основной playbook идёт несколько минут: Docker, Nginx и сертификаты Let's Encrypt,
PostgreSQL, Synapse, веб-приложения, LiveKit, Coturn, UFW, Fail2ban. В конце
автоматически запускается verifier; успешная установка заканчивается строкой
`All required checks passed.`

## 7. Проверить результат

```bash
matrix-deploy verify
```

- Element Web: `https://element.<домен>`, вход `admin` с паролем из шага 6;
- админка Ketesa: `https://synad.<домен>`, вход тем же `@admin`;
- Element Call: `https://call.<домен>`.

## Если что-то пошло не так

- **Preflight ругается на DNS, AAAA или занятые порты** — исправьте причину и
  запустите `matrix-deploy converge --admin-password` (топология уже сохранена,
  повторно её вводить не нужно).
- **Установка прервалась посередине** (обрыв SSH, ошибка сети) —
  `matrix-deploy converge --admin-password`. Пароль нужен только если учётная
  запись `@admin` ещё не создана; в `/etc/matrix-deploy` он не сохраняется.
- **Не скачивается образ** (`i/o timeout` к `ghcr.io` или Docker Hub). Установщик
  сам пробует upstream, официальный альтернативный реестр и публичные зеркала,
  всегда по зафиксированному digest. Если не помогло ни одно, добавьте своё
  зеркало или прокси в `/etc/matrix-deploy/deployment.yml`, например:

  ```yaml
  matrix_registry_proxy: "http://user:password@proxy.example:3128"
  # или/и свои зеркала (полностью заменяют список по умолчанию):
  matrix_registry_mirrors:
    docker.io: [mirror.gcr.io, dockerhub.timeweb.cloud]
    ghcr.io: [ghcr.nju.edu.cn, ghcr.m.daocloud.io]
  ```

  и запустите `matrix-deploy converge --admin-password`.
- **PostgreSQL не поднялся при первой установке** (`Wait for PostgreSQL ...
  Connection refused`, контейнера `postgres` нет в `docker ps`). В версиях до
  исправления первого запуска контейнер перезапускался посреди инициализации
  базы и оставлял её недоделанной. Пока Synapse ни разу не запускался, в базе
  нет данных, и её можно безопасно пересоздать:

  ```bash
  docker logs --tail 50 postgres        # посмотреть причину
  docker rm -f postgres
  rm -rf /opt/matrix/postgres/data/pgdata
  git -C /opt/matrix-deploy/source pull
  matrix-deploy converge --admin-password
  ```

  На работающей установке с пользователями так делать **нельзя** — это удалит
  базу.
- **Повторный `./bootstrap.sh`** на уже настроенном сервере намеренно
  отклоняется: он перезаписал бы топологию и обновил бы версии без backup.
  Используйте `matrix-deploy converge`.
- **Начать с нуля** — `matrix-deploy destroy` (сначала сделает полный backup и
  дважды попросит подтверждение), затем снова шаг 6.

## Повседневные операции

```bash
matrix-deploy verify              # проверка стека; --deep добавит certbot renew --dry-run
matrix-deploy converge            # повторно применить конфигурацию (версии не меняются)
matrix-deploy upgrade             # backup -> актуальные stable-версии -> применение
matrix-deploy backup              # backup БД и конфигурации (--include-media — с медиа)
matrix-deploy check               # ansible --check --diff без изменений
matrix-deploy version             # установленная версия установщика
```

**Обновление самого плейбука** при установке из git:

```bash
git -C /opt/matrix-deploy/source pull
cd /opt/matrix-deploy/source && ./bootstrap.sh --prepare-only   # если сменились пины ansible/коллекций
matrix-deploy converge
```

`matrix-deploy update` предназначен для установки из GitHub Release и на
git-клоне намеренно отказывает, чтобы не заменить клон архивом.

Если после обновления плейбука `converge` сообщает, что версии в lock старше
поддерживаемых (`locked versions are older than this playbook supports`),
выполните `matrix-deploy upgrade`: он сделает backup и обновит версии.
