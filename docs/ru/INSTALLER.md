# Установка одной командой

[English](../INSTALLER.md) | **Русский**

Установка из git-клона под root пошагово описана в [`INSTALL.md`](INSTALL.md).

Каналом распространения являются GitHub Releases
(<https://github.com/guestikchatgpt/matrix_deploy/releases>).

## Первая установка

```bash
curl -fsSL https://raw.githubusercontent.com/guestikchatgpt/matrix_deploy/main/install.sh | sudo bash
```

`install.sh`:

1. определяет последний stable-релиз через `/releases/latest` и принимает только
   теги вида `vX.Y.Z`;
2. скачивает `matrix-deploy-<tag>.tar.gz` и `SHA256SUMS` и сверяет SHA-256
   именно этого архива;
3. запускает `bootstrap.sh --install` из распакованного релиза.

`bootstrap.sh --install` копирует дерево исходников в
`/opt/matrix-deploy/source` (предыдущая версия остаётся в `source.previous`),
записывает тег в `/opt/matrix-deploy/installed-release`, ставит CLI
`/usr/local/sbin/matrix-deploy` и продолжает обычный `bootstrap.sh` уже из
установленной копии: venv, зафиксированный ansible-core и коллекции, затем
интерактивный `deploy.sh`.

Для автоматизированной подготовки хоста без интерактивного деплоя:

```bash
curl -fsSL .../install.sh | sudo MATRIX_DEPLOY_NONINTERACTIVE=1 bash
# позже, интерактивно:
sudo /opt/matrix-deploy/source/bootstrap.sh
```

Повторный `deploy.sh`/`bootstrap.sh` поверх уже существующей установки
(`/etc/matrix-deploy/deployment.yml`) намеренно отклоняется: он перезаписал бы
топологию и обновил бы версии без backup. Для существующей установки
используйте команды ниже.

## После установки

```bash
sudo matrix-deploy converge            # повторно применить конфигурацию с текущим version lock
sudo matrix-deploy converge --admin-password   # продолжить прерванную первую установку
sudo matrix-deploy upgrade             # backup -> обновление version lock -> converge
sudo matrix-deploy verify [--deep]     # проверка стека (--deep: certbot renew --dry-run)
sudo matrix-deploy check               # ansible --check --diff
sudo matrix-deploy backup [--include-media]
sudo matrix-deploy destroy             # с backup и двумя подтверждениями
sudo matrix-deploy update              # обновить сам установщик до последнего релиза
sudo matrix-deploy version
```

`update` меняет только исходники установщика/playbook: скачивает и проверяет
новый релиз, подготавливает его `.venv` (`bootstrap.sh --prepare-only`) и при
ошибке откатывается на предыдущую версию. Matrix-стек при этом не меняется —
изменения playbook применяются явным `matrix-deploy converge`, обновление
приложений — явным `matrix-deploy upgrade`.

Состояние установщика (`/opt/matrix-deploy/installed-release`) хранится отдельно
от состояния деплоя (`/etc/matrix-deploy`), поэтому `destroy` не теряет
информацию об установленной версии установщика.

## Публикация релиза

Push тега `vX.Y.Z` запускает `.github/workflows/release.yml`: `git archive`
тегированного дерева с единым top-level каталогом и `SHA256SUMS` публикуются как
assets не-prerelease релиза.

## Тесты

`tests/installer-smoke.sh` (запускается от root, в CI — через `sudo`) собирает
фейковый релиз на локальной ФС и через `file://` проверяет: установку,
отказ при неверном SHA-256 и prerelease-теге, диспетчеризацию CLI и
`update-source.sh` с подготовкой нового дерева.
