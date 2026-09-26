# Matrix Deploy

[English](README.md) | **Русский**

[![Release](https://img.shields.io/github/v/release/guestikchatgpt/matrix_deploy)](https://github.com/guestikchatgpt/matrix_deploy/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Текущий релиз: [v1.0.0](https://github.com/guestikchatgpt/matrix_deploy/releases/tag/v1.0.0).**

Разворачивает собственный мессенджер на [Matrix](https://matrix.org) — с
веб-клиентом, админкой, аудио- и видеозвонками — на одном чистом сервере
**Ubuntu 24.04 LTS**. Установка интерактивная: вы отвечаете на несколько
вопросов (домен, email, пароль администратора), остальное делается
автоматически, включая TLS-сертификаты, firewall и финальную самопроверку.

## Что вы получаете

| Адрес | Что это |
| --- | --- |
| `https://element.<домен>` | **Element Web** — веб-клиент для переписки и звонков |
| `https://call.<домен>` | **Element Call** — отдельное приложение для видеозвонков |
| `https://synad.<домен>` | **Ketesa** — админка сервера (пользователи, комнаты, медиа) |
| `matrix.<домен>` | **Synapse** — сам Matrix-сервер; ID пользователей вида `@user:matrix.<домен>` |

Ещё два адреса — служебные: их не открывают в браузере, но без них не работают
звонки, и DNS-записи для них обязательны.

| Адрес | Что это |
| --- | --- |
| `rtc.<домен>` | **LiveKit** и сервис авторизации звонков — через него идут групповые звонки Element Call и Element X |
| `turn.<домен>` | **Coturn** (TURN) — помогает звонкам 1:1 пройти через NAT и firewall |

Подключаться можно и мобильными клиентами (Element X и другие), указав
сервер `matrix.<домен>`. Первый администратор — `@admin`, его пароль задаётся
при установке. Федерацию с другими Matrix-серверами можно включить или
выключить.

## Как это устроено

- **Ansible на самом сервере.** Репозиторий кладётся на сервер и запускается
  там же; отдельная машина с Ansible не нужна. Нужные версии Ansible ставятся
  автоматически в изолированное окружение.
- **Приложения в Docker:** PostgreSQL, Synapse, Element Web, Element Call,
  Ketesa, LiveKit и сервис авторизации звонков `lk-jwt-service`.
- **На хосте:** Nginx (единая HTTPS-точка входа) с автоматическими
  сертификатами Let's Encrypt, Coturn, firewall UFW и защита SSH через Fail2ban.
- **Звонки:** групповые звонки Element Call идут через LiveKit, классические
  звонки 1:1 — через Coturn.
- **Версии:** при установке берутся актуальные стабильные релизы (без beta/rc) и
  фиксируются. Обычное повторное применение конфигурации версии не меняет;
  обновление — только явной командой `upgrade`, и перед ним всегда делается
  backup.
- **Устойчивая загрузка образов:** если GitHub или Docker Hub недоступны,
  образы берутся из альтернативных реестров и публичных зеркал — строго по
  зафиксированному digest, поэтому подменить их нельзя. Можно задать свой прокси.
- **Самопроверка:** после установки и по команде `verify` проверяются сервисы,
  контейнеры, сертификаты, firewall и эндпоинты.

Подробности реализации — в [техдокументации](docs/ru/TECHNICAL.md).

## Требования

- чистый сервер **Ubuntu 24.04 LTS** (другие версии, в том числе 26.04, пока не
  поддерживаются), доступ под `root`;
- от 2 vCPU и 2 GiB RAM (рекомендуется 4 GiB), от 10 GiB свободного места;
- публичный IPv4 (прямой или через NAT с пробросом портов);
- домен и **заранее созданные** шесть DNS A-записей на IP сервера: `matrix`,
  `element`, `synad`, `call`, `rtc`, `turn` (без AAAA-записей) — см.
  [«Сначала — DNS»](#сначала--dns-до-запуска-установки);
- открытые входящие порты — список в [инструкции по установке](docs/ru/INSTALL.md#0-что-подготовить-заранее).

## Установка

### Сначала — DNS (до запуска установки!)

До начала установки создайте у своего DNS-провайдера **шесть A-записей**, все
на публичный IPv4 сервера. Пример для домена `example.com` и сервера
`203.0.113.10`:

| Имя | Тип | Значение |
| --- | --- | --- |
| `matrix.example.com` | A | `203.0.113.10` |
| `element.example.com` | A | `203.0.113.10` |
| `synad.example.com` | A | `203.0.113.10` |
| `call.example.com` | A | `203.0.113.10` |
| `rtc.example.com` | A | `203.0.113.10` |
| `turn.example.com` | A | `203.0.113.10` |

Почему заранее:

- установщик в самом начале проверяет эти записи и **остановится**, если хоть
  одной нет или она указывает не туда;
- во время установки для всех этих имён выпускаются сертификаты Let's Encrypt, а
  это возможно только когда DNS уже смотрит на сервер.

AAAA-записей (IPv6) для этих имён быть не должно. Новые записи могут
расходиться от нескольких минут до пары часов; проверить можно командой
`dig +short A matrix.example.com` — она должна вернуть IP сервера. Другие
префиксы вместо `matrix`, `element` и т. д. можно выбрать при установке, тогда
и записи создавайте с ними.

### Затем — сама установка

Под `root` подготовьте `curl`, затем запустите установщик. Он скачает последний
стабильный GitHub Release и проверит SHA-256 архива перед запуском:

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/guestikchatgpt/matrix_deploy/main/install.sh -o /root/matrix-deploy-install.sh && bash /root/matrix-deploy-install.sh
```

Или клонируйте конкретный тег релиза (также под `root`):

```bash
apt-get update && apt-get install -y git
mkdir -p /opt/matrix-deploy
git clone --branch v1.0.0 https://github.com/guestikchatgpt/matrix_deploy.git /opt/matrix-deploy/source
cd /opt/matrix-deploy/source
./bootstrap.sh --install
```

Установщик задаст вопросы, проверит сервер и DNS, покажет план и после
подтверждения всё развернёт. **Пошагово, с подготовкой DNS и портов и
разбором ошибок — [`docs/INSTALL.md`](docs/ru/INSTALL.md).**

## Команды

После установки всё управление — через команду `matrix-deploy` (под `root`):

| Команда | Что делает |
| --- | --- |
| `matrix-deploy verify` | проверить, что всё работает (`--deep` — ещё и тест продления сертификатов) |
| `matrix-deploy converge` | повторно применить конфигурацию (версии приложений не меняются) |
| `matrix-deploy converge --admin-password` | продолжить прерванную первую установку |
| `matrix-deploy upgrade` | обновить приложения до актуальных стабильных версий (сначала backup) |
| `matrix-deploy backup` | резервная копия БД и конфигурации (`--include-media` — вместе с файлами пользователей) |
| `matrix-deploy check` | показать, что изменилось бы, ничего не меняя |
| `matrix-deploy destroy` | удалить установку (сначала полный backup, два подтверждения) |
| `matrix-deploy update` | обновить установщик из GitHub Release; для git-клона используйте команды ниже |
| `matrix-deploy version` | версия установщика |

Те же операции доступны скриптами в корне репозитория: `converge.sh`,
`upgrade.sh`, `verify.sh`, `backup.sh`, `check.sh`, `destroy.sh`.

Если установка сделана из git-клона с тегом `v1.0.0`, обновляйте его переходом
на нужный следующий тег. Такой клон находится в detached HEAD, поэтому обычный
`git pull` в нём не сработает:

```bash
git -C /opt/matrix-deploy/source fetch --tags
git -C /opt/matrix-deploy/source checkout vX.Y.Z  # укажите опубликованный новый тег
/opt/matrix-deploy/source/bootstrap.sh --prepare-only
matrix-deploy converge
```

Если `converge` сообщает, что новые playbook требуют более свежих версий
приложений, выполните `matrix-deploy upgrade` (перед обновлением он делает backup).

## Файлы

**В репозитории**

| Путь | Назначение |
| --- | --- |
| `bootstrap.sh` | подготовка сервера и запуск установки |
| `deploy.sh` | интерактивные вопросы и первый деплой |
| `converge.sh`, `upgrade.sh`, `verify.sh`, `backup.sh`, `check.sh`, `destroy.sh` | операции над установкой |
| `install.sh`, `scripts/` | установка из релиза и CLI `matrix-deploy` |
| `ansible/` | playbook и роли |
| `docs/` | документация |
| `tools/`, `tests/` | служебные инструменты и тесты |

**На сервере**

| Путь | Что там |
| --- | --- |
| `/opt/matrix-deploy/source` | установщик (этот репозиторий) |
| `/etc/matrix-deploy/deployment.yml` | ваши ответы при установке: домен, email, режим сети |
| `/etc/matrix-deploy/versions.yml` | зафиксированные версии приложений |
| `/opt/matrix/` | данные и конфигурация сервисов |
| `/opt/matrix/.secrets/` | сгенерированные пароли и ключи (только для root) |
| `/var/backups/matrix-deploy/` | резервные копии |

## Документация

- [`docs/INSTALL.md`](docs/ru/INSTALL.md) — пошаговая установка на сервер;
- [`docs/TECHNICAL.md`](docs/ru/TECHNICAL.md) — как всё устроено внутри;
- [`docs/INSTALLER.md`](docs/ru/INSTALLER.md) — установщик из релизов и выпуск релизов;
- [`docs/RESTORE.md`](docs/ru/RESTORE.md) — формат резервных копий и восстановление;
- [`docs/ROADMAP.md`](docs/ru/ROADMAP.md) — планы и статус разработки.

## Статус

**v1.0.0** — первый стабильный релиз. Проверено на реальном сервере Ubuntu
24.04: чистая установка с настоящими DNS и сертификатами Let's Encrypt,
федерация с другим Matrix-сервером, аудио- и видеозвонки между клиентами из
разных сетей. Что ещё не проверено и что в планах — в
[`docs/ROADMAP.md`](docs/ru/ROADMAP.md). Список изменений — на странице
[Releases](https://github.com/guestikchatgpt/matrix_deploy/releases).

## Лицензия

[MIT](LICENSE).
