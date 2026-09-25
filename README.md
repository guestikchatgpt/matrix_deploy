# Matrix Deploy

Развёртывание Matrix Synapse на чистом сервере Ubuntu 24.04 LTS с помощью Ansible.

В репозитории намеренно сохранены два независимых контура звонков:

- **классические звонки Matrix** используют системный сервис Coturn;
- **MatrixRTC / Element Call** используют LiveKit с собственным встроенным TURN-сервером.

Эти TURN-стеки независимы по архитектуре и не должны объединяться.

> Статус: ветка hardening / подготовка релиз-кандидата. Уже работают статические
> проверки, syntax CI и runtime CI для bootstrap/preflight, включая реальное
> динамическое определение stable-версий upstream. До слияния этой ветки в `main`
> и до использования её как production-релиза всё ещё требуется полный
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

## Как выбираются версии приложений

Версии приложений **не захардкожены в playbook** и не берутся через плавающий
`:latest` во время запуска контейнеров.

`ansible/inventory/group_vars/all/versions.yml` содержит только политику
разрешения версий: upstream GitHub-репозитории, Docker image repositories,
правила преобразования release tag -> image tag и разрешённый major PostgreSQL.

Во время первого preflight Matrix Deploy:

1. получает latest stable release каждого приложения через GitHub Releases;
2. исключает draft/prerelease через семантику stable release;
3. для Docker Hub-образов дополнительно проверяет соответствующий tag через
   Docker Hub API;
4. для всех Docker Hub/GHCR-образов проверяет реальную доступность выбранного
   image/tag и архитектуры хоста через `skopeo`;
5. для PostgreSQL выбирает последний опубликованный stable patch в разрешённом
   major, используя metadata Docker Official Images;
6. атомарно сохраняет точный набор выбранных версий в
   `/etc/matrix-deploy/versions.yml` с режимом `0600`;
7. после установки Docker заранее скачивает именно эти exact image refs, а затем
   запускает контейнеры с теми же refs.

Таким образом, новая установка получает актуальные stable-версии на момент
развёртывания, но весь конкретный deploy остаётся воспроизводимым.

Обычный `converge.sh` снова проверяет upstream и сообщает о доступных обновлениях,
но **не переписывает version lock и не обновляет контейнеры самовольно**.
Явное обновление выполняется через `upgrade.sh`: сначала backup, затем refresh
lock-файла, затем converge с новым exact-набором.

Для каждого компонента в политике задан `min_version` — минимальный stable-релиз,
на котором проверена текущая конфигурация (это не пин). Resolver никогда не
выберет версию ниже, а обычный `converge.sh` откажется применять конфигурацию к
lock со старыми версиями и попросит выполнить `upgrade.sh`. Текущие минимумы
(2026-09-25): Synapse v1.161.0, Element Web v1.12.29, Element Call v0.26.0,
LiveKit v1.13.7, lk-jwt-service 0.7.0, Ketesa v1.5.0, PostgreSQL 18.6.

PostgreSQL является специальным случаем: его major задаётся политикой
`postgresql_major` и автоматически не повышается. Resolver выбирает только
последний stable release внутри разрешённого major. Смена major PostgreSQL —
отдельная миграционная операция.

Если GitHub/Docker registry временно недоступны при обычном converge, существующий
lock сохраняется. При первом deploy или явном `upgrade.sh` невозможность
разрешить/проверить версии является ошибкой: развёртывание не должно начинаться с
непроверенным набором образов.

## Первое развёртывание

> **Пошаговая инструкция для сервера под root (Ubuntu 24.04): [`docs/INSTALL.md`](docs/INSTALL.md).**

Предполагаемая схема работы — Ansible с локальным контроллером: всё выполняется
непосредственно на целевом сервере.

Установка из stable GitHub Release одной командой (подробно — `INSTALLER.md`):

```bash
curl -fsSL https://raw.githubusercontent.com/guestikchatgpt/matrix_deploy/main/install.sh | sudo bash
```

После неё все операции доступны через CLI `matrix-deploy` (`converge`,
`upgrade`, `verify`, `check`, `backup`, `destroy`, `update`).

Либо из git-клона:

```bash
apt-get install -y git
git clone https://github.com/guestikchatgpt/matrix_deploy.git /opt/matrix-deploy/source
cd /opt/matrix-deploy/source
./bootstrap.sh --install
```

`bootstrap.sh`:

1. требует Ubuntu 24.04 LTS (`noble`) и запуск от root;
2. устанавливает зависимости контроллера, включая `skopeo` для проверки registry;
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
разрешает и фиксирует текущие stable-версии в `/etc/matrix-deploy/versions.yml`,
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
- доступность apt-репозиториев;
- актуальные stable releases приложений;
- существование соответствующих Docker image tags;
- доступность выбранного image для архитектуры текущего хоста;
- целостность runtime version lock.

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

Используются:

```text
/etc/matrix-deploy/deployment.yml
/etc/matrix-deploy/versions.yml
```

Preflight проверяет, появились ли новые stable releases upstream, но version lock
при обычном converge не меняет. Поэтому повторное применение конфигурации не
является скрытым обновлением приложений.

Если существующая учётная запись администратора уже создана, пароль
администратора Matrix повторно не запрашивается.

Повторный запуск `bootstrap.sh`/`deploy.sh` на хосте, где уже есть
`/etc/matrix-deploy/deployment.yml`, отклоняется: он перезаписал бы топологию и
обновил бы версии без backup.

Если первое развёртывание прервалось после сохранения топологии, но до создания
начальной учётной записи `@admin`, можно продолжить без повторного ввода
топологии:

```bash
sudo ./converge.sh --admin-password
```

Пароль восстановления вводится без отображения, хранится только во временном
файле в `/run/matrix-deploy/` на время запуска Ansible и удаляется shell trap.
В `/etc/matrix-deploy` он не сохраняется.

Флаг `--refresh-versions` существует для явного обновления lock-файла и обычно
вызывается через `upgrade.sh`, а не вручную.

### Ansible check/diff

```bash
sudo ./check.sh
```

`check.sh` намеренно работает только с уже развёрнутой управляемой установкой.
Перед запуском Ansible он требует существующий `/etc/matrix-deploy/versions.yml`,
все постоянные сгенерированные секреты и обе полные пары certificate/key. Если
управляемое состояние неполное, скрипт требует реального converge/recovery.

Затем запускается preflight, после него — `site.yml --check --diff`. Preflight
может проверить наличие более новых upstream releases, но `check.sh` никогда не
создаёт и не обновляет version lock.

Runtime reconciliation и health-check'и, которые невозможно осмысленно
симулировать, в check mode пропускаются. При этом состояние SAN сертификатов всё
равно считывается, а playbook сообщает, потребовалась бы reconciliation каждой
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
секреты и конфигурация, `/etc/matrix-deploy` вместе с deployment/version lock,
signing key/appservice state Synapse, состояние сертификатов, инвентари
Docker/UFW и manifest. Для файлов backup принудительно устанавливается режим
`0600` только для root; это особенно важно для `docker-inspect.json`, который
может содержать секреты из environment контейнеров.

Сырая директория данных PostgreSQL намеренно не архивируется: PostgreSQL
резервируется логически через `pg_dump`.

Обычный backup **не включает** потенциально большой media store, если явно не
указан `--include-media`.

### Явное обновление до актуальных stable-версий upstream

```bash
sudo ./upgrade.sh
```

`upgrade.sh` требует подтверждения и сначала создаёт backup. Затем preflight
заново опрашивает upstream, проверяет registry и **атомарно заменяет**
`/etc/matrix-deploy/versions.yml` новым exact-набором. Docker-role заранее
скачивает выбранные image refs, после чего выполняется обычный converge.

Это единственный штатный workflow, который намеренно двигает application
versions вперёд. Обычный `converge.sh` lock не изменяет.

Major PostgreSQL при этом автоматически не меняется. Для него обновляется только
stable patch в пределах `postgresql_major`; guard по major version/data directory
не позволяет молча запустить несовместимую базу.

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

## MatrixRTC: как клиенты находят LiveKit

Актуальная модель upstream (проверено 2026-09-25 по документации Element Call,
Synapse 1.161.0 и lk-jwt-service 0.7.0):

- **Основной путь** — хоумсервер. `homeserver.yaml` содержит
  `matrix_rtc.transports`, а `experimental_features.msc4143_enabled` включает
  эндпоинт `GET /_matrix/client/unstable/org.matrix.msc4143/rtc/transports`
  (MSC4519, требует токен). Начиная с Element Call v0.24.0 discovery через
  `.well-known` объявлен устаревшим.
- **Fallback** — `org.matrix.msc4143.rtc_foci` в `/.well-known/matrix/client`
  сохраняется для клиентов, которые ещё читают его (Element-клиенты оставили его
  как запасной путь; Synapse сам этот ключ не генерирует).
- `livekit_service_url` в `matrix_rtc.transports` устарел в Synapse 1.161.0,
  но upstream требует оставлять его для совместимости. Новое свойство `url`
  подразумевает lk-jwt-service в режиме application service (MSC4195/MSC4512),
  который upstream пока называет экспериментальным, поэтому оно не включено.
- lk-jwt-service проверяет OpenID-токены пользователей через
  `/_matrix/federation/v1/openid/userinfo`, находя хоумсервер через
  `/.well-known/matrix/server`. Поэтому при выключенной федерации Synapse
  включает listener-ресурс `openid`, а `.well-known/matrix/server` по-прежнему
  указывает на `:443`; сама федерация остаётся выключенной
  (`federation_domain_whitelist: []`, ресурса `federation` нет, 8448 закрыт).
- LiveKit отправляет события участников на `http://127.0.0.1:8084/sfu_webhook`
  lk-jwt-service (delegated delayed leave, MSC4140); дополнительно включена
  pull-проверка `LIVEKIT_SANITY_CHECK_INTERVAL_SECONDS=60`.

Verifier проверяет все звенья: transports-эндпоинт (401 без токена = включён),
OpenID userinfo, `.well-known` fallback и webhook.

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
/etc/matrix-deploy/versions.yml         exact version lock, выбранный preflight
/opt/matrix/.secrets/                   сгенерированные исходные секреты (только root)
/opt/matrix/                            управляемое состояние приложений
/var/www/matrix/                        Matrix well-known + ACME webroot
/var/backups/matrix-deploy/             защищённые резервные копии
/usr/local/sbin/matrix-stack-verify     установленный verifier
/opt/matrix-deploy/                     установленный релиз установщика и CLI matrix-deploy
```

Не добавляйте в Git `/etc/matrix-deploy`, `.venv`, сгенерированные секреты,
сертификаты или архивы backup.

Системная учётная запись `matrix` намеренно не входит в группу Docker: доступ к
Docker socket фактически эквивалентен root и приложениям не требуется.

## CI и критерии релиза

GitHub Actions выполняет:

- проверки YAML и статических invariants, включая запрет возвращения hardcoded
  top-level `*_image` pins в version policy, плавающих `:latest` и известных
  deprecated-паттернов Ansible;
- реальный запуск `bootstrap.sh --prepare-only` на Ubuntu 24.04;
- реальный runtime preflight с запросами к GitHub Releases, Docker Official
  Images/Docker Hub и registry validation через `skopeo`;
- проверку, что повторный обычный preflight заново проверяет upstream, но не
  изменяет SHA существующего `/etc/matrix-deploy/versions.yml`;
- проверку manifest всех **динамически выбранных** image refs с обязательной
  поддержкой `linux/amd64` и `linux/arm64`;
- проверку синтаксиса shell и Python resolver;
- настоящий NAT-mode preflight на runner Ubuntu 24.04 с DNS A/AAAA-запросами и
  проверками ресурсов, портов, routing и apt;
- разбор inventory;
- `ansible-playbook --syntax-check` для playbook развёртывания, preflight,
  verification, backup и destroy;
- реальный рендер Jinja verifier с включённой и отключённой федерацией и
  `bash -n` для обоих вариантов.

Версии `ansible-core` и collections по-прежнему фиксируются в репозитории, потому
что это runtime самого установщика, а не обновляемый Matrix application stack.
Конфигурация Ansible отключает deprecated top-level fact injection; роли
используют `ansible_facts[...]`. Сторонние apt-репозитории управляются через
deb822.

Эти CI-gate необходимы, но недостаточны. Динамический resolver подтверждает
существование и стабильность отдельных upstream releases, но не заменяет
интеграционную проверку совместимости всего набора как единого стека.

Перед слиянием релиз-кандидата нужно пройти оставшиеся этапы из `ROADMAP.md`:
полное развёртывание на чистой Ubuntu с реальными DNS/ACME, второй
converge/idempotence, `check.sh`, reboot, Certbot dry-run, федерацию, классический
TURN и MatrixRTC-звонки, а также rehearsal backup/destroy/restore до включения
автоматического восстановления.
