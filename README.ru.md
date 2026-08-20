# sshai

<p align="center"><img src="assets/readme/banner-ru.svg" width="100%" alt="sshai: удалённые команды и компактные доказательства"></p>

`sshai` — Go CLI для AI-агентов, запускающий неинтерактивные команды Linux bash и Windows PowerShell 7 по SSH. Полный вывод сохраняется локально, а в контекст агента возвращается компактный паспорт запуска.

Удалённые команды внутрь. Компактные доказательства наружу. English version: [README.md](README.md).

## Для кого

Используйте `sshai` для уже авторизованных неинтерактивных команд на хостах из `ssh_config`, когда нужны доказательства без загрузки большого вывода в диалог агента. Инструмент разделяет транспорт, доказательства и операционные полномочия.

## Возможности

- Запуск на одном или нескольких SSH-алиасах Linux/bash либо Windows/PowerShell 7.
- Локальное хранение вывода и компактный паспорт с исходом и путём к артефакту.
- Запросы, сравнения и поиск по артефактам без повторной загрузки всего результата.
- Файлы тел для многострочных команд, JSON-результаты, дельты и именованные контексты.
- Fail-closed для интерактивных программ, PowerShell 5.1, secret stdin, произвольных identities и неподдерживаемых two-hop сценариев.

```text
command -> SSH transport -> captured artifact -> bounded passport -> local q / diff / log
```

## Быстрый старт

Требуются Go `1.26.5` или новее в ветке `1.26`, OpenSSH и локально настроенный SSH-алиас.

```bash
git clone https://github.com/aprudkin/sshai.git
cd sshai
go test ./...
scripts/install.sh
sshai run web01 -- df -h
```

Не передавайте многострочное тело через аргументы процесса:

```bash
sshai run --body-file check.ps1 win01
```

Для автоматизации используйте версионированный JSON-envelope:

```bash
sshai run --result-format=json web01 -- uname -a
```

Полный список команд — `sshai help`, контракт запуска — `sshai help run`.

## Граница безопасности

`sshai` — инструмент транспорта и доказательств. Он не выдаёт полномочий на доступ к хосту или изменение его состояния.

- Выполняйте только отдельно авторизованные действия на настроенных алиасах.
- Не передавайте в командах или артефактах пароли, токены, ключи, сертификаты и ожидаемый секретный вывод.
- Для многострочных тел используйте `--body-file`; для secret stdin и передачи файлов нужен отдельный утверждённый workflow.
- Policy denial, transport failure и удалённый non-zero exit — разные исходы.

См. [agent usage](docs/agent-usage.md) для default-use и fallback правил.

## Разработка и вклад

```bash
go test ./...
go vet ./...
go build ./...
```

Интеграционные тесты требуют доступных тестовых хостов и намеренно не входят в CI:

```bash
go test -tags=integration ./...
```

Перед issue или pull request прочтите [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) и [Code of Conduct](CODE_OF_CONDUCT.md).

## Статус и benchmarks

Версия 1 включает `run`, `q`, `diff`, `log`, `hosts`, `gc` и `help`; Linux- и Windows-доказательства сохранены в репозитории.

Контролируемый benchmark v1.1 показал 99,86% снижения оценочного видимого tool output, но лишь 44,71% снижения суммарных Codex input tokens. Решение — **needs work**, а не подтверждённый результат. См. [полный отчёт](docs/benchmarks/v1.1-results-2026-08-13.md).

Историческое fan-out исследование v2.1 завершилось **inconclusive**. Amendment 2 — утверждённый кандидат на измерение, а не результат; он ещё не заморожен для измерения. См. [протокол](docs/benchmarks/v2.1-protocol.md) и [определение анализатора](docs/benchmarks/v2.1-analyzer.md).

## Лицензия

MIT. См. [LICENSE](LICENSE).
