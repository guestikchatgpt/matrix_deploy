# Дорожная карта hardening Matrix Deploy

Этот репозиторий начался с Ansible baseline, импортированного до production-исправлений реального Matrix-стека, выполненных 2026-08-18.

Цель остаётся прежней: переиспользуемый установщик для чистого сервера Ubuntu 24.04 LTS. Production-состояние служит эталонной реализацией, а не источником жёстко заданных доменов, IP-адресов, учётных данных или одноразовых предположений о конкретном хосте.

## Инварианты

- Основная целевая ОС — Ubuntu 24.04 LTS.
- Повторный запуск playbook должен быть безопасным.
- Системный Coturn для классических звонков и встроенный TURN LiveKit — отдельные, намеренно независимые TURN-стеки; объединять их нельзя.
- HTTP/API-порт LiveKit 7880 должен оставаться внутренним; публичной HTTPS-точкой входа является Nginx.
- Федерация — реальный feature flag и должна последовательно управлять Synapse, Nginx, UFW и verification. Исключение: `.well-known/matrix/server` публикуется всегда, потому что через него lk-jwt-service находит OpenID-эндпоинт для MatrixRTC.
- Секреты не должны попадать в Git.
- Перед destructive-операциями и обновлениями существующая установка должна резервироваться.
- Обычный converge не должен незаметно обновлять application images.
- Новое развёртывание должно использовать актуальные stable releases upstream, но конкретный deploy обязан оставаться воспроизводимым через exact runtime lock.
- Major PostgreSQL не должен повышаться автоматически.

## P0 — исправления, проверенные на production

1. Заменить старый образ Synapse Admin на Ketesa, сохранив существующее имя роли на время функционального рефакторинга.
2. Перевести lk-jwt-service на безопасную модель конфигурации:
   - использовать `LIVEKIT_JWT_BIND` вместо deprecated-конфигурации порта;
   - явно задавать `LIVEKIT_FULL_ACCESS_HOMESERVERS`;
   - webhook LiveKit -> lk-jwt-service `/sfu_webhook` добавлять только с версии, где он есть в релизе (lk-jwt-service >= 0.7.0; добавлено 2026-09-25).
3. Сохранить `LiveKit room.auto_create=false`.
4. Явно задать relay range встроенного TURN LiveKit: 63000-63999/udp.
5. Сохранить диапазон media LiveKit RTC 62000-62999/udp и TCP fallback 7881/tcp.
6. Не публиковать LiveKit 7880/tcp через UFW.
7. Копировать сертификаты Let's Encrypt в стабильную директорию сертификатов LiveKit и монтировать в контейнер именно директорию.
8. Сделать deploy hook Certbot зависимым от certificate lineage:
   - Matrix SAN certificate: reload Nginx, обновление/перезапуск только LiveKit;
   - legacy TURN certificate: обновление/перезапуск только Coturn.
9. Во всех HTTP vhost напрямую обслуживать `/.well-known/acme-challenge/` из общего webroot до редиректа остального трафика на HTTPS.
10. Сверять фактический набор SAN сертификата вместо использования только `creates:`.
11. Ограничить публичную маршрутизацию `/_synapse/` необходимыми client/admin endpoints; метрики оставить закрытыми.
12. Явно включать метрики Synapse, когда они настроены.
13. Расширить deny ranges для SSRF-защиты URL preview Synapse.
14. Добавить точечные `denied-peer` ranges Coturn, не блокируя вслепую все RFC1918-сети.
15. Создать отдельную роль Fail2ban, начав с базовой защиты SSH; Matrix/Nginx-фильтры включать только после проверки regex на реальных логах.
16. Заменить исходный verification script на production-проверенную модель verifier: без ANSI escape-последовательностей в non-TTY output, без ложных ошибок `pipefail`/`grep -q` и с явной проверкой обоих TURN-стеков.

## PostgreSQL

- Заменить неиспользуемую/агрессивную модель tuning на детерминированную конфигурацию под управлением Ansible.
- Использовать консервативные defaults с учётом ресурсов; не задавать огромный `work_mem` на каждую операцию.
- Исправить readiness-логику PostgreSQL: проверять результат модуля, а не общий успех task.
- Добавить guard major-version/data-directory перед обновлением контейнера.
- Сохранить поведение PostgreSQL 18 с `PGDATA=/var/lib/postgresql/data/pgdata`.
- Resolver версий должен автоматически двигаться только по stable patch releases внутри `postgresql_major`; смена major остаётся отдельной миграцией.

## Версии и обновления

Целевая модель больше не предполагает ручное обновление release tags в Git каждые несколько недель.

- `ansible/inventory/group_vars/all/versions.yml` содержит **политику источников**, а не текущие application versions.
- Для приложений latest stable release определяется через GitHub Releases.
- Docker Hub images дополнительно сверяются через Docker Hub API.
- Все Docker Hub/GHCR refs проверяются через registry inspection для архитектуры хоста.
- Для PostgreSQL используется metadata Docker Official Images и выбирается последний stable patch в разрешённом major.
- Результат preflight сохраняется как exact runtime lock `/etc/matrix-deploy/versions.yml`.
- Docker-role заранее скачивает именно exact refs из lock до запуска application stack.
- Обычный `converge.sh` проверяет upstream, но не меняет lock.
- `upgrade.sh` сначала создаёт backup, затем явно обновляет lock и применяет новый набор.
- При недоступности upstream обычный converge может продолжать работать с существующим lock; fresh deploy/explicit upgrade должны завершаться ошибкой, если новый набор невозможно надёжно разрешить и проверить.
- `site.yml` не должен выполнять deployment mutation без валидного version lock.
- Плавающие `:latest` в runtime lock и hardcoded top-level `*_image` pins в repository policy запрещены static checks.
- CI должен реально выполнять resolver, проверять exact refs и multiarch manifests.
- CI должен доказывать, что повторный обычный preflight не переписывает существующий lock.
- Версии `ansible-core` и collections остаются зафиксированными как runtime самого установщика и обновляются отдельно после compatibility validation.
- Не использовать deprecated top-level injection Ansible facts; роли должны обращаться через `ansible_facts[...]`, а injection должна быть отключена.
- Сторонние apt-репозитории должны управляться через deb822 sources, а не deprecated task `apt_repository`.

## Nginx и ACME

- Гарантировать фактический reload bootstrap-конфигурации Nginx до первого запуска Certbot.
- Проверять HTTP-01 routing до запроса сертификатов у Let's Encrypt.
- Сохранить общий SAN-сертификат для Synapse, Element, Ketesa, Element Call и LiveKit.
- TURN-сертификат оставить отдельной lineage.
- Добавить deep verification workflow для `certbot renew --dry-run --run-deploy-hooks`, а не запускать его при каждом converge.

## LiveKit / MatrixRTC

- Discovery транспорта (с 2026-09): основной путь — эндпоинт хоумсервера `GET /_matrix/client/unstable/org.matrix.msc4143/rtc/transports` (MSC4519) из `matrix_rtc.transports` в `homeserver.yaml`; `.well-known` `org.matrix.msc4143.rtc_foci` — только deprecated fallback для клиентов (Element Call v0.24.0).
- `matrix_rtc.transports[].livekit_service_url` deprecated с Synapse 1.161.0, но обязателен для обратной совместимости. Новое свойство `url` включать только вместе с режимом application service у lk-jwt-service (MSC4195/MSC4512) — upstream пока помечает его experimental.
- lk-jwt-service проверяет OpenID-токены через `/_matrix/federation/v1/openid/userinfo`, находя хоумсервер через `.well-known/matrix/server` (иначе `:8448`). Поэтому при выключенной федерации нужны listener `openid` и `m.server` в `.well-known/matrix/server`.
- `room.auto_create=false`.
- `LIVEKIT_FULL_ACCESS_HOMESERVERS` должен содержать только локальное Matrix server name и необходимые локальные domain aliases.
- Встроенный TURN: 3480/udp, 5449/tcp, relay 63000-63999/udp.
- RTC: 7881/tcp и 62000-62999/udp.
- Публичный вход через Nginx остаётся на 443; TURN/TLS на 443 — отдельная архитектурная задача, требующая другого IP или L4/SNI routing.

## Классический Coturn

- Сохранить порты 3478/tcp+udp, 5349/tcp+udp и relay 57000-57999/udp.
- Различать установки с прямым публичным IP и установки за NAT. Нельзя универсально считать, что `relay-ip == external-ip`.
- Проверять relay отдельно от встроенного TURN LiveKit.

## Synapse

- Сделать `synapse_enable_federation` реальным end-to-end switch либо удалить его; предпочтительно протянуть его через все связанные роли.
- Сделать operational policy явными переменными (`report_stats`, политика user-directory, metrics).
- Сохранять текущие настройки MatrixRTC delayed events и rate limits, пока upstream requirements не изменятся.
- Заменить filesystem marker администратора как source of truth реальной проверкой состояния Synapse/user.

## Firewall и IPv6

- Формировать firewall rules из единой модели переменных вместо дублирующихся жёстко заданных списков.
- Определять активный SSH-порт до включения deny-incoming policy UFW.
- Применять базовую firewall policy достаточно рано, чтобы сервисы не оказывались публично доступны во время развёртывания.
- Рассматривать IPv6 как явно поддерживаемый или неподдерживаемый режим. AAAA-запись при отключённом IPv6 должна выявляться preflight, а не молча приниматься.

## Bootstrap / UX оператора

Добавить operator-facing bootstrap/launcher вокруг Ansible, который до развёртывания выполняет:

- проверку Ubuntu/version и privileges;
- проверку RAM/CPU/disk;
- проверку internet/apt/dependencies;
- определение внешнего IPv4 с подтверждением оператором;
- генерацию и подтверждение domain/FQDN;
- проверку A/AAAA;
- проверку конфликтов локальных портов;
- определение NAT/direct-public;
- определение SSH-порта;
- обнаружение существующей installation/config/certificates;
- определение и registry-validation актуальных stable application versions;
- сохранение exact version lock;
- вывод явного deployment plan и запрос подтверждения.

Bootstrap остаётся orchestration UX; логика Matrix-сервисов должна находиться в Ansible roles.

## Lifecycle workflows

Предоставить явные workflows для backup, upgrade, rollback metadata и destroy. Destroy должен требовать строгого подтверждения и не должен незаметно удалять последнюю резервную копию. Runtime version lock входит в operational state и должен попадать в backup/restore.

## Критерии проверки

Перед тем как считать релиз-кандидат завершённым, нужно пройти:

1. syntax/static checks;
2. runtime resolution stable-версий и проверку registry;
3. развёртывание на чистой Ubuntu 24.04;
4. второй запуск Ansible без непредусмотренных изменений и без изменения version lock;
5. проверку desired state установленного хоста через `check.sh`;
6. тест сохранения состояния после reboot;
7. Certbot dry-run с deploy hooks;
8. проверку входящей и исходящей федерации;
9. проверку relay классического TURN;
10. локальные и federated MatrixRTC audio/video calls, по возможности с проверкой встроенного TURN relay;
11. rehearsal backup/destroy/restore на disposable host;
12. финальный verifier без обязательных FAIL.

## Статус реализации (v1.0.0)

### Реализовано в коде

- [x] Универсальная модель topology/subdomains; в исполняемом коде нет hard-coded production domain/IP.
- [x] Динамический resolver актуальных stable application releases через GitHub Releases + Docker Official Images/Docker Hub + registry validation.
- [x] Exact runtime version lock `/etc/matrix-deploy/versions.yml`; application release tags больше не требуют ручного изменения в репозитории.
- [x] PostgreSQL автоматически обновляется только внутри разрешённого major.
- [x] Docker-role заранее скачивает exact image refs из lock до запуска application stack.
- [x] Обычный converge не переписывает lock; explicit upgrade выполняет backup -> refresh lock -> converge.
- [x] CI реально выполняет dynamic resolver и проверяет каждый выбранный image в registry, включая manifest linux/amd64 и linux/arm64.
- [x] Static checks запрещают возврат hardcoded top-level application image pins и `:latest`.
- [x] Зафиксированы версии controller/collections для воспроизводимости самого установщика.
- [x] Интерактивный bootstrap/launcher с сохранением topology, input validation и preflight.
- [x] CI реально запускает `bootstrap.sh --prepare-only` на runner Ubuntu 24.04.
- [x] CI реально запускает NAT-mode preflight с настоящим DNS A/AAAA resolution и проверками ресурсов, портов, routing и apt.
- [x] Различаются direct-public/NAT, проверяется точное соответствие local IP и определяется порт активной SSH-сессии.
- [x] DNS validation использует прямые A/AAAA queries, поэтому IPv4-mapped результаты NSS не вызывают ложных AAAA failures.
- [x] Явное поведение при отключённом IPv6 с запретом неожиданных AAAA.
- [x] Ansible roles используют `ansible_facts[...]`, deprecated top-level fact injection отключена.
- [x] Docker и Nginx repositories используют `deb822_repository`; deprecated `apt_repository` запрещён static check.
- [x] Детерминированная конфигурация PostgreSQL, консервативный tuning и major-version guard.
- [x] Настроены Synapse policy/metrics/federation и database-backed reconciliation администратора.
- [x] Recovery path для частичного deployment с временной передачей секрета через `converge.sh --admin-password`.
- [x] Исправлен Element permalink и выполнен переход на Ketesa.
- [x] Production-проверенная security model LiveKit/JWT MatrixRTC и явный relay range встроенного TURN.
- [x] Независимый hardened-контур классического Coturn с поддержкой NAT mapping.
- [x] ACME-safe bootstrap/production routing Nginx, SAN reconciliation, recovery неполной lineage и selective deploy hook.
- [x] Desired-state UFW, включая удаление устаревшего публичного правила 7880.
- [x] Отдельная роль Fail2ban с базовым SSH jail и обработкой race control socket.
- [x] Production-проверенный параметризованный verifier и необязательный deep Certbot renewal test.
- [x] NAT-safe self-verification через loopback с корректными Host/SNI без требования hairpin NAT.
- [x] Ограничены Docker container logs; service account Matrix не получает привилегии через группу Docker.
- [x] `converge.sh`, check-mode-aware `check.sh`, backup-first `upgrade.sh` и защищённый `destroy.sh`.
- [x] Защищённый формат backup с root-only artifacts; destructive destroy требует полный backup с media Synapse.
- [x] GitHub Actions gate для static/YAML/shell/Python/Ansible syntax.
- [x] CI рендерит и shell-валидирует варианты verifier с включённой и отключённой федерацией.
- [x] README оператора и явный restore runbook/contract.
- [x] Установка одной командой из stable GitHub Release (`install.sh`, проверка SHA-256, `bootstrap.sh --install`) и CLI `matrix-deploy` поверх converge/upgrade/verify/check/backup/destroy; `matrix-deploy update` с откатом при неудачной подготовке нового релиза.
- [x] Повторный `deploy.sh` поверх существующей установки отклоняется (нет скрытого перезаписывания топологии и обновления lock без backup).
- [x] При отключённой федерации Synapse блокирует и исходящую федерацию (`federation_domain_whitelist: []`); verifier не требует метрик при `synapse_enable_metrics=false`.
- [x] MatrixRTC приведён к актуальной модели upstream (2026-09-25): discovery через `rtc/transports` хоумсервера, `.well-known` как fallback; OpenID для lk-jwt-service работает и при выключенной федерации; LiveKit webhook -> `/sfu_webhook` для delegated leave; конфиги Element Web/Element Call/Ketesa по текущим схемам; verifier проверяет transports, OpenID и webhook.
- [x] `min_version` в политике версий: resolver не выбирает версии ниже проверенных, converge отказывается применять конфиг к более старому lock (нужен `upgrade.sh`).

### Намеренно отложено до integration testing

- [ ] Автоматический `restore.sh`. Формат backup и последовательность restore документированы, но automation не включается до успешного полного destroy/restore test.
- [ ] Matrix/Nginx-фильтры Fail2ban. Добавлять только после проверки `fail2ban-regex` на реальных логах.
- [ ] Полноценный режим развёртывания IPv6.
- [ ] TURN/TLS на публичном TCP 443; нужен отдельный IP или специально спроектированный L4/SNI frontend.

### Release gates, для которых всё ещё нужен disposable/реальный Ubuntu-хост

- [x] Полная чистая установка Ubuntu 24.04 через `bootstrap.sh` с реальными DNS проекта и Let's Encrypt, используя динамически выбранный exact version lock.
- [ ] Второй запуск `converge.sh` без непредусмотренных изменений и без изменения lock.
- [ ] `check.sh` на установленном хосте; проверить полезность diff и отсутствие ложных runtime failures.
- [ ] Reboot и проверка persistence.
- [ ] `verify.sh --deep` / staging renewal Certbot на clean-host deployment.
- [x] Проверка входящей и исходящей федерации, если она включена (приглашения и комнаты с другим сервером).
- [ ] Authenticated relay test классического Coturn.
- [x] MatrixRTC audio/video calls между клиентами из разных сетей. Отдельная проверка embedded TURN relay ещё не проводилась.
- [ ] Полный rehearsal backup -> destroy -> restore до включения автоматического restore; восстановить именно сохранённый version lock, а не молча выбирать новые версии во время disaster recovery.
- [ ] Финальный verifier без обязательных FAIL на release-candidate host.
