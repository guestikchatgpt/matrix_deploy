# Дорожная карта hardening Matrix Deploy

Этот репозиторий начался с исходного Ansible baseline, импортированного до production-исправлений Matrix-стека `redacted.ru`, выполненных 2026-08-18.

Цель остаётся прежней: переиспользуемый установщик для чистого сервера Ubuntu 24.04 LTS. Production-состояние служит эталонной реализацией, а не источником жёстко заданных доменов, IP-адресов, учётных данных или одноразовых предположений о конкретном хосте.

## Инварианты

- Основная целевая ОС — Ubuntu 24.04 LTS.
- Повторный запуск playbook должен быть безопасным.
- Системный Coturn для классических звонков и встроенный TURN LiveKit — отдельные, намеренно независимые TURN-стеки; объединять их нельзя.
- HTTP/API-порт LiveKit 7880 должен оставаться внутренним; публичной HTTPS-точкой входа является Nginx.
- Федерация — реальный feature flag и должна последовательно управлять Synapse, Nginx, UFW, well-known данными и verification.
- Секреты не должны попадать в Git.
- Перед destructive-операциями и обновлениями существующая установка должна резервироваться.
- Обычный converge не должен незаметно обновлять все container images.

## P0 — исправления, проверенные на production

1. Заменить старый образ Synapse Admin на Ketesa, сохранив существующее имя роли на время функционального рефакторинга.
2. Перевести lk-jwt-service на безопасную модель конфигурации:
   - использовать `LIVEKIT_JWT_BIND` вместо deprecated-конфигурации порта;
   - явно задавать `LIVEKIT_FULL_ACCESS_HOMESERVERS`;
   - не добавлять неподдерживаемую конфигурацию webhook.
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

## Версии и обновления

- Ввести протестированную матрицу версий контейнеров.
- Обычный запуск `site.yml` должен применять конфигурацию, не обновляя контейнерные образы незаметно.
- Обновления должны быть отдельным явным workflow: backup, compatibility checks, deployment, verification и информация для rollback.
- Зафиксировать версии Ansible collections после проверки совместимости.
- Каждый application image закрепить на опубликованном release tag; плавающие `latest` в матрице релиз-кандидата не допускаются.
- Проверять каждый pinned image в CI через registry и требовать наличие manifest для linux/amd64 и linux/arm64.
- Не использовать deprecated top-level injection Ansible facts; роли должны обращаться через `ansible_facts[...]`, а injection должна быть отключена.
- Сторонние apt-репозитории должны управляться через deb822 sources, а не deprecated task `apt_repository`.

## Nginx и ACME

- Гарантировать фактический reload bootstrap-конфигурации Nginx до первого запуска Certbot.
- Проверять HTTP-01 routing до запроса сертификатов у Let's Encrypt.
- Сохранить общий SAN-сертификат для Synapse, Element, Ketesa, Element Call и LiveKit.
- TURN-сертификат оставить отдельной lineage.
- Добавить deep verification workflow для `certbot renew --dry-run --run-deploy-hooks`, а не запускать его при каждом converge.

## LiveKit / MatrixRTC

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
- вывод явного deployment plan и запрос подтверждения.

Bootstrap остаётся orchestration UX; логика Matrix-сервисов должна находиться в Ansible roles.

## Lifecycle workflows

Предоставить явные workflows для backup, upgrade, rollback metadata и destroy. Destroy должен требовать строгого подтверждения и не должен незаметно удалять последнюю резервную копию.

## Критерии проверки

Перед тем как считать релиз-кандидат завершённым, нужно пройти:

1. syntax/static checks;
2. развёртывание на чистой Ubuntu 24.04;
3. второй запуск Ansible без непредусмотренных изменений;
4. проверку desired state установленного хоста через `check.sh`;
5. тест сохранения состояния после reboot;
6. Certbot dry-run с deploy hooks;
7. проверку входящей и исходящей федерации;
8. проверку relay классического TURN;
9. локальные и federated MatrixRTC audio/video calls, по возможности с проверкой встроенного TURN relay;
10. rehearsal backup/destroy/restore на disposable host;
11. финальный verifier без обязательных FAIL.

## Статус реализации — `agent/roadmap-hardening`

### Реализовано в коде

- [x] Универсальная модель topology/subdomains; в исполняемом коде нет hard-coded production domain/IP.
- [x] Зафиксированы версии controller/collections и release tags всех application images.
- [x] CI проверяет каждый application image в registry, включая manifest linux/amd64 и linux/arm64.
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
- [x] GitHub Actions gate для static/YAML/shell/Ansible syntax.
- [x] CI рендерит и shell-валидирует варианты verifier с включённой и отключённой федерацией.
- [x] README оператора и явный restore runbook/contract.

### Намеренно отложено до integration testing

- [ ] Автоматический `restore.sh`. Формат backup и последовательность restore документированы, но automation не включается до успешного полного destroy/restore test.
- [ ] Matrix/Nginx-фильтры Fail2ban. Добавлять только после проверки `fail2ban-regex` на реальных логах.
- [ ] Полноценный режим развёртывания IPv6.
- [ ] TURN/TLS на публичном TCP 443; нужен отдельный IP или специально спроектированный L4/SNI frontend.

### Release gates, для которых всё ещё нужен disposable/реальный Ubuntu-хост

- [ ] Полная чистая установка Ubuntu 24.04 через `bootstrap.sh` с реальными DNS проекта и Let's Encrypt.
- [ ] Второй запуск `converge.sh` без непредусмотренных изменений.
- [ ] `check.sh` на установленном хосте; проверить полезность diff и отсутствие ложных runtime failures.
- [ ] Reboot и проверка persistence.
- [ ] `verify.sh --deep` / staging renewal Certbot на clean-host deployment.
- [ ] Проверка входящей и исходящей федерации, если она включена.
- [ ] Authenticated relay test классического Coturn.
- [ ] Локальные и federated MatrixRTC audio/video calls; подтвердить доступность embedded TURN relay.
- [ ] Полный rehearsal backup -> destroy -> restore до включения автоматического restore.
- [ ] Финальный verifier без обязательных FAIL на release-candidate host.
