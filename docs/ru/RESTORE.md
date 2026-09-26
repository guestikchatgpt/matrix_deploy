# Статус восстановления и формат резервной копии

[English](../RESTORE.md) | **Русский**

Автоматическое восстановление намеренно **пока не включено**. Формат backup уже
достаточно структурирован для создания restore automation, но восстановление
Matrix identity/database/media — destructive-операция, которую сначала нужно
доказать на disposable-хосте Ubuntu 24.04.

## Содержимое резервной копии

Каждая директория `/var/backups/matrix-deploy/<timestamp>/` содержит:

- `MANIFEST.txt`;
- `synapse.pgdump` — логический dump PostgreSQL в custom format;
- `config-state.tar.gz` — topology развёртывания, runtime version lock,
  сгенерированные секреты, конфигурацию/signing key Synapse, конфигурацию
  приложений, состояние Nginx/Coturn/Fail2ban, состояние Let's Encrypt и, при
  необходимости, media store Synapse;
- `docker-inspect.json`;
- `docker-images.txt`;
- `ufw-status.txt`.

В частности, backup `/etc/matrix-deploy` должен сохранять оба файла:

```text
/etc/matrix-deploy/deployment.yml
/etc/matrix-deploy/versions.yml
```

`versions.yml` — это exact-набор application images, с которым работала
резервируемая установка. При disaster recovery его нужно восстанавливать вместе
с topology, а не заново выбирать latest stable releases во время самого restore.
Обновить restored deployment до новых stable-версий можно позже отдельным
`upgrade.sh`, уже после успешной проверки восстановления.

Сырая директория данных PostgreSQL в backup не включается. Restore должен
инициализировать тот же разрешённый major PostgreSQL, который указан в
восстановленном version lock/policy, и загрузить `synapse.pgdump` через
`pg_restore`.

## Требование к media

Для disaster recovery после `destroy.sh` используйте только backup, в manifest
которого указано:

```text
media_store_included=true
```

`destroy.sh` принудительно создаёт такой backup перед удалением развёртывания.
Обычный `backup.sh` или pre-upgrade backup вполне может содержать
`media_store_included=false`, потому что при обычном converge/upgrade живая media
директория не удаляется.

## Планируемая последовательность восстановления

Проверенный integration-тестом restore workflow должен выполнять следующие этапы
именно в таком порядке:

1. проверить backup manifest, указанное Matrix server name и наличие runtime
   version lock;
2. потребовать чистый/совместимый target с Ubuntu 24.04 и совместимую версию
   репозитория;
3. подготовить зафиксированное Ansible-окружение (`bootstrap.sh --prepare-only`);
4. восстановить `/etc/matrix-deploy/deployment.yml`,
   `/etc/matrix-deploy/versions.yml`, исходные секреты, signing key, состояние
   сертификатов и media/config-файлы из `config-state.tar.gz`;
5. проверить, что exact images из восстановленного lock всё ещё доступны в
   registry; **не обновлять lock автоматически**;
6. развернуть только базовый host baseline + Docker + Nginx + пустой экземпляр
   PostgreSQL нужного major;
7. восстановить `synapse.pgdump` в пустую базу Synapse через `pg_restore` с явно
   заданной семантикой ownership/clean;
8. развернуть Synapse и остальные application/TURN roles с exact image refs из
   восстановленного lock;
9. выполнить обычную и deep verification;
10. проверить login, federation, классический TURN и MatrixRTC calls до объявления
    restore завершённым.

Нельзя заменять эту последовательность простым копированием сырой директории
данных PostgreSQL. В playbook специально предусмотрен PostgreSQL major-version
guard, который не допускает неявных major upgrades или запуска несовместимого
data directory.

Также нельзя использовать disaster recovery как скрытый upgrade. Restore сначала
должен воспроизвести сохранённое состояние, и только после успешной проверки
можно отдельно выполнить `upgrade.sh`, который заново разрешит актуальные stable
releases upstream.

## Почему `restore.sh` пока отсутствует

Даже синтаксически корректный restore script может нарушить согласованность
identity, signing key, media, database или набора application versions, если
этапы выполняются в неправильном порядке. `restore.sh` будет добавлен в этот
репозиторий только после того, как описанная выше последовательность успешно
пройдёт clean-host destroy/restore integration test с использованием полного
backup.
