# Matrix Deploy

Развёртывание Matrix Synapse на чистом сервере Ubuntu 24.04 LTS с помощью Ansible.

В репозитории намеренно сохранены два независимых контура звонков:

- **классические звонки Matrix** используют системный сервис Coturn;
- **MatrixRTC / Element Call** используют LiveKit с собственным встроенным TURN-сервером.

Эти TURN-стеки независимы по архитектуре и не должны объединяться.

> Статус: ветка hardening / подготовка релиз-кандидата. Уже работают статические
> проверки, syntax CI и runtime CI для bootstrap/preflight. До слияния этой ветки
> в `main` и до использования её как production-релиза всё ещё требуется полный
> интеграционный тест на чистом сервере с реальными DNS проекта и ACME.

## Что разворачивается

- PostgreSQL 18 для Synapse;
- Matrix Synapse;
- Element Web;
- Ketesa — интерфейс администрирования Synapse;
- Element Call;
- LiveKit;
- Element `lk-jwt-service` для авторизации MatrixRTC;
- системный Coturn для классических звонков;
- Nginx и Certbot на хосте;
- UFW;
- базовая защита SSH через Fail2ban.

Версии контейнеров и приложений зафиксированы в
`ansible/inventory/group_vars/all/versions.yml`. Плавающие теги `latest` в
матрице релиз-кандидата не допускаются.

## Первое развёртывание

Предполагаемая схема работы — Ansible с локальным контроллером: репозиторий
клонируется непосредственно на целевой сервер и запускается там.

```bash
git clone https://github.com/guestikchatgpt/matrix_deploy.git
cd matrix_deploy
git switch agent/roadmap-hardening   # пока этот PR не слит
sudo ./bootstrap.sh
```

`bootstrap.sh`:

1. требует Ubuntu 24.04 LTS (`noble`) и запуск от root;
2. устанавливает небольшой набор зависимостей контроллера;
3. создаёт `.venv`;
4. устанавливает зафиксированные версии `ansible-core` и коллекций;
5. запускает интерактивный launcher `deploy.sh`.

Launcher запрашивает:

- базовый домен и префиксы имён сервисных хостов;
- email для Let's Encrypt;
- включение или отключение федерации;
- подтверждение публичного IPv4;
- топологию TURN: NAT или прямой публичный IP;
- начальный пароль администратора Matrix.

Он определяет порт активной SSH-сессии, проверяет ввод оператора, сохраняет
несекретную топологию в `/etc/matrix-deploy/deployment.yml`, запускает preflight,
выводит план развёртывания и требует подтверждения перед запуском основного
playbook.

Чтобы только подготовить Ansible-окружение без запуска интерактивного
развёртывания:

```bash
sudo ./bootstrap.sh --prepare-only
```

## Контракт preflight

Новое развёртывание отклоняется, если не выполнены базовые предпосылки. Preflight
проверяет:

- Ubuntu 24.04 LTS;
- фактические права root;
- минимальные CPU/RAM/свободное место на диске;
- корректность публичного и relay IPv4;
- точное соответствие локального IP выбранному режиму direct-public или NAT;
- DNS A-записи всех сервисных имён Matrix без лишних дополнительных A-адресов;
- неожиданные AAAA-записи при отключённом IPv6;
- конфликты портов на чистом хосте;
- доступность apt-репозиториев.

Проверка DNS A/AAAA выполняет прямые DNS-запросы, а не использует результат
NSS-подобного разрешения имён. Поэтому IPv4-mapped IPv6-адреса не могут быть
ошибочно приняты за опубликованные AAAA-записи.

IPv6 сейчас **не реализован** как production-режим развёртывания. Публикация
AAAA-записей при `matrix_ipv6_enabled=false` считается ошибкой конфигурации, а не
игнорируется с видимостью поддержки IPv6.

## Штатные операции

### Повторное применение конфигурации существующей установки

```bash
sudo ./converge.sh
```

Используется `/etc/matrix-deploy/deployment.yml`. Если существующая учётная запись
администратора уже создана, пароль администратора Matrix повторно не
запрашивается.

Если первое развёртывание прервалось после сохранения топологии, но до создания
начальной учётной записи `@admin`, можно продолжить без повторного ввода
топологии:

```bash
sudo ./converge.sh --admin-password
```

Пароль восстановления вводится без отображения, хранится только во временном
файле в `/run/matrix-deploy/` на время запуска Ansible и удаляется shell trap.
В `/etc/matrix-deploy` он не сохраняется.

### Ansible check/diff

```bash
sudo ./check.sh
```

`check.sh` намеренно работает только с уже развёрнутой управляемой установкой.
Перед запуском Ansible он требует наличия всех постоянных сгенерированных
секретов и обеих полных пар certificate/key. Если управляемое состояние неполное,
скрипт завершает работу и требует реального converge/recovery вместо того, чтобы
позволять check mode побочно создавать отсутствующий секрет или сертификат.

Затем запускается preflight, после него — `site.yml --check --diff`. Runtime-
reconciliation и health-check'и, которые невозможно осмысленно симулировать,
в check mode пропускаются. При этом состояние SAN сертификатов всё равно
считывается, а playbook сообщает, потребовалась бы reconciliation каждой
линейки или нет.

Check mode — инструмент проверки desired state, а не замена реального converge и
финального verifier.

### Проверка состояния

```bash
sudo ./verify.sh
```

Установленный verifier проверяет системные сервисы, контейнеры, listeners, UFW,
PostgreSQL, client/federation endpoints Synapse, поведение локальных/закрытых
метрик, веб-приложения, MatrixRTC discovery, оба TURN-стека, TLS-сертификаты,
соответствие копий сертификатов, маршрутизацию Certbot/ACME, Fail2ban и синтаксис
Nginx.

Обычный verifier намеренно **NAT-safe**. Локальные self-check'и сервисов
подключаются к listeners через `127.0.0.1`, сохраняя корректные HTTP Host и TLS
SNI. Поэтому NAT-развёртыванию не нужен hairpin NAT только ради проверки
собственной конфигурации Nginx/TURN.

Это также означает, что обычный verifier не заявляет о подтверждении внешней
доступности из Интернета. Внешняя федерация, TURN relay и поведение MatrixRTC
остаются release/integration-тестами.

Для более тяжёлой симуляции продления сертификатов:

```bash
sudo ./verify.sh --deep
```

Дополнительно выполняется:

```text
certbot renew --dry-run --run-deploy-hooks
```

Staging-проверка Certbot проверяет внешнюю доступность ACME и одновременно
тестирует deploy hooks, привязанные к конкретным certificate lineage.

### Резервное копирование

Обычный backup конфигурации и базы данных:

```bash
sudo ./backup.sh
```

Полный backup с включением media store Synapse:

```bash
sudo ./backup.sh --include-media
```

Резервные копии сохраняются в `/var/backups/matrix-deploy/<timestamp>/` с режимом
`0700`. В них входят логический dump PostgreSQL в custom format, управляемые
секреты и конфигурация, signing key/appservice state Synapse, состояние
сертификатов, инвентари Docker/UFW и manifest. Для файлов backup принудительно
устанавливается режим `0600` только для root; это особенно важно для
`docker-inspect.json`, который может содержать секреты из environment контейнеров.

Сырая директория данных PostgreSQL намеренно не архивируется: PostgreSQL
резервируется логически через `pg_dump`.

Обычный backup **не включает** потенциально большой media store, если явно не
указан `--include-media`.

### Обновление до версий, зафиксированных в текущем checkout

```bash
sudo ./upgrade.sh
```

`upgrade.sh` требует подтверждения, сначала создаёт backup, затем выполняет
обычный converge. Штатный `converge.sh` не использует `pull: true`, поэтому он не
является неявной операцией «обновить всё до latest». Для PostgreSQL дополнительно
есть guard по major version и data directory, поэтому изменение настроенной
major-версии не сможет молча запустить несовместимый образ базы данных.

### Удаление развёртывания

```bash
sudo ./destroy.sh
```

Destroy намеренно защищён дополнительными подтверждениями. Требуется ввести
точные строки:

```text
BACKUP <matrix-server-name>
DESTROY <matrix-server-name>
```

Перед вторым подтверждением создаётся **полный backup с media store**.
Destroy удаляет контейнеры и данные Matrix, состояние Matrix в Nginx/Coturn, обе
certificate lineage Matrix и Matrix-специфичные правила firewall. При этом
сохраняются:

- `/var/backups/matrix-deploy`;
- установленные системные пакеты;
- firewall-доступ по SSH;
- общие правила firewall для HTTP/HTTPS.

Автоматическое восстановление пока намеренно не предоставляется. См.
`docs/RESTORE.md`.

## Модель публичной сети

Публичные порты по умолчанию:

| Назначение | Протокол/порт |
| --- | --- |
| HTTP / ACME | TCP 80 |
| HTTPS / веб-вход Matrix | TCP 443 |
| Федерация, если включена | TCP 8448 |
| Классический Coturn | TCP+UDP 3478 |
| Классический Coturn TLS/DTLS | TCP+UDP 5349 |
| Relay классического Coturn | UDP 57000-57999 |
| TCP fallback LiveKit RTC | TCP 7881 |
| Media LiveKit RTC | UDP 62000-62999 |
| Встроенный TURN LiveKit | UDP 3480 |
| Встроенный TURN/TLS LiveKit | TCP 5449 |
| Relay встроенного TURN LiveKit | UDP 63000-63999 |

Следующие сервисы являются локальными/backend-сервисами и не должны намеренно
публиковаться через UFW:

- LiveKit HTTP/API `7880`;
- Synapse `8008`;
- PostgreSQL `5432`;
- loopback-порты Element/Ketesa/Call/JWT `8081-8084`.

TURN/TLS на публичном TCP 443 не входит в эту архитектуру, поскольку Nginx уже
занимает 443 на том же публичном IP. Для такого fallback нужен отдельный IP или
явно спроектированный L4/SNI frontend.

## Сертификаты

Используются две lineage Let's Encrypt:

1. SAN-сертификат, именованный по hostname Synapse и покрывающий Synapse, Element,
   Ketesa, Element Call и LiveKit;
2. отдельный сертификат классического TURN.

Каждый HTTP vhost напрямую обслуживает `/.well-known/acme-challenge/` из общего
webroot до перенаправления обычного HTTP-трафика. Роль сверяет фактический набор
SAN, умеет восстанавливать неполную lineage и не использует `creates:` как
источник истины о состоянии сертификата.

Deploy hook Certbot учитывает конкретную lineage:

- при продлении Matrix SAN перезагружается Nginx и обновляется/перезапускается
  только LiveKit;
- при продлении TURN обновляется/перезапускается только Coturn.

LiveKit использует стабильную копию сертификата в `/opt/matrix/livekit/certs`, а
не bind mount отдельных файлов-целей symlink из Let's Encrypt.

## Endpoint Synapse Admin

Общее правило hardening Synapse — не публиковать лишние endpoints `/_synapse/`.
Это развёртывание публикует только `/_synapse/client/` и `/_synapse/admin/`, а для
остального пространства `/_synapse/` возвращает 404.

`/_synapse/admin/` здесь является осознанным архитектурным решением: Ketesa —
браузерный интерфейс администрирования и ему нужен Synapse Admin API. Доступ всё
равно требует авторизации администратора Matrix. Метрики Synapse остаются
закрытыми и проверяются только локально.

## Fail2ban

Первая поддерживаемая политика Fail2ban намеренно консервативна: jail `sshd` с
автоматически определённым SSH-портом, backend `systemd` и действием через UFW.

Matrix/Nginx-специфичные фильтры не включаются, пока их regex не будут проверены
на реальных логах развёртывания с помощью `fail2ban-regex`. Избежать ложных ban
важнее, чем добавить непроверенные фильтры.

## Состояние и секреты

Важные пути:

```text
/etc/matrix-deploy/deployment.yml       сохранённая топология и выбор оператора
/opt/matrix/.secrets/                   сгенерированные исходные секреты (только root)
/opt/matrix/                            управляемое состояние приложений
/var/www/matrix/                        Matrix well-known + ACME webroot
/var/backups/matrix-deploy/             защищённые резервные копии
/usr/local/sbin/matrix-stack-verify     установленный verifier
```

Не добавляйте в Git `/etc/matrix-deploy`, `.venv`, сгенерированные секреты,
сертификаты или архивы backup.

Системная учётная запись `matrix` намеренно не входит в группу Docker: доступ к
Docker socket фактически эквивалентен root и приложениям не требуется.

## CI и критерии релиза

GitHub Actions выполняет:

- проверки YAML и статических invariants, включая запрет плавающих ссылок на
  образы `:latest` и известных deprecated-паттернов Ansible;
- проверку manifest в registry для каждого зафиксированного application image с
  обязательной поддержкой `linux/amd64` и `linux/arm64`;
- проверку синтаксиса shell;
- реальный запуск `bootstrap.sh --prepare-only` на Ubuntu 24.04 с зафиксированными
  версиями контроллера и коллекций;
- реальный запуск `preflight.yml` в NAT-режиме на чистом runner Ubuntu 24.04 с
  настоящими DNS A/AAAA-запросами и проверками ресурсов, портов, routing и apt;
- разбор inventory;
- `ansible-playbook --syntax-check` для playbook развёртывания, preflight,
  verification, backup и destroy;
- реальный рендер Jinja verifier с включённой и отключённой федерацией, после
  чего для обоих отрендеренных скриптов выполняется `bash -n`.

В конфигурации Ansible отключена deprecated-инъекция facts в top-level
переменные; роли используют `ansible_facts[...]`. Управление сторонними
apt-репозиториями выполнено в формате deb822.

Эти CI-gate необходимы, но недостаточны. Перед слиянием релиз-кандидата нужно
пройти оставшиеся интеграционные этапы из `ROADMAP.md`: полное развёртывание на
чистой Ubuntu с реальными DNS/ACME, второй converge/idempotence, `check.sh`,
проверку сохранения состояния после reboot, Certbot dry-run, федерацию,
классический TURN и MatrixRTC-звонки, а также rehearsal backup/destroy/restore до
включения автоматического восстановления.
