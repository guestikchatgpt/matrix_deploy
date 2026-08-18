# Статус восстановления и формат резервной копии

Автоматическое восстановление намеренно **пока не включено**. Формат backup уже
достаточно структурирован для создания restore automation, но восстановление
Matrix identity/database/media — destructive-операция, которую сначала нужно
доказать на disposable-хосте Ubuntu 24.04.

## Содержимое резервной копии

Каждая директория `/var/backups/matrix-deploy/<timestamp>/` содержит:

- `MANIFEST.txt`;
- `synapse.pgdump` — логический dump PostgreSQL в custom format;
- `config-state.tar.gz` — topology развёртывания, сгенерированные секреты,
  конфигурацию/signing key Synapse, конфигурацию приложений, состояние
  Nginx/Coturn/Fail2ban, состояние Let's Encrypt и, при необходимости, media
  store Synapse;
- `docker-inspect.json`;
- `docker-images.txt`;
- `ufw-status.txt`.

Сырая директория данных PostgreSQL в backup не включается. Restore должен
инициализировать зафиксированную major-версию PostgreSQL и загрузить
`synapse.pgdump` через `pg_restore`.

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

1. проверить backup manifest и указанное Matrix server name;
2. потребовать чистый/совместимый target с Ubuntu 24.04 и совместимую версию
   репозитория;
3. подготовить зафиксированное Ansible-окружение (`bootstrap.sh --prepare-only`);
4. восстановить `/etc/matrix-deploy/deployment.yml`, исходные секреты, signing key,
   состояние сертификатов и media/config-файлы из `config-state.tar.gz`;
5. развернуть только базовый host baseline + Docker + Nginx + пустой экземпляр
   PostgreSQL;
6. восстановить `synapse.pgdump` в пустую базу Synapse через `pg_restore` с явно
   заданной семантикой ownership/clean;
7. развернуть Synapse и остальные application/TURN roles;
8. выполнить обычную и deep verification;
9. проверить login, federation, классический TURN и MatrixRTC calls до объявления
   restore завершённым.

Нельзя заменять эту последовательность простым копированием сырой директории
данных PostgreSQL. В playbook специально предусмотрен PostgreSQL major-version
guard, который не допускает неявных major upgrades или запуска несовместимого
data directory.

## Почему `restore.sh` пока отсутствует

Даже синтаксически корректный restore script может нарушить согласованность
identity, signing key, media или database, если этапы выполняются в неправильном
порядке. `restore.sh` будет добавлен в этот репозиторий только после того, как
описанная выше последовательность успешно пройдёт clean-host destroy/restore
integration test с использованием полного backup.
