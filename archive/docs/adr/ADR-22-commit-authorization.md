# ADR-22. Единственная точка записи и CommitAuthorization

Дата: 23.11.2026
Статус: accepted

## Контекст

Шаг 5.3 требует убрать автоподтверждение опасных планов (CR-1/CR-2) и
сделать запись доказуемо безопасной. До шага:

- `app/patching/commit.py` сам ставил `plan.confirmed=True`;
- `CoreServices.write_patch` вызывал `PatchWriter` напрямую;
- `PatchEngine.commit` существовал только как заглушка
  `PatchCommitUnavailable`;
- план и снимок не были связаны с разрешением на запись.

## Решение

1. В `core/patching/commit.py` введён `PatchCommitService` — единственная
   точка записи. Он вызывает `PatchWriter` только после проверки
   `CommitAuthorization`.
2. `CommitAuthorization` содержит:
   - `plan_id` — хеш плана и содержимого сгенерированного патча;
   - `snapshot_id` — снимок, для которого построен план;
   - `confirmed_risks` — явно подтверждённые ключи опасных изменений;
   - служебный `_token`, который выпускает только сервис.
   Вручную созданный токен, токен другого сервиса, другой план или
   изменившийся снимок отклоняются.
3. `PatchCommitService.authorize` отказывает, если опасное изменение не
   подтверждено. `commit` после авторизации выставляет legacy-флаг
   `plan.confirmed=True` только внутри сервиса — это не автоподтверждение
   вызывающего кода.
4. `commit` выполняет post-write verification: читает записанные файлы и
   сверяет с патчем. При ошибке записи (`overwrite=False`) удаляются уже
   записанные файлы, частичный мод не остаётся.
5. `commit` требует обязательный `expected_snapshot_id` — текущий
   `forest.cache_id`. Если он пуст или план построен для другого снимка,
   запись отклоняется. Use case дополнительно сверяет все элементы
   предпросмотра с текущим снимком до первой записи.
6. `PatchCommitService` и `PatchWriter` отклоняют план с ошибками и план
   без `snapshot_id`. Проверка продублирована в низкоуровневом writer'е.
7. Запись набора элементов транзакционна на уровне use case: при ошибке
   позднего элемента удаляются все файлы, записанные этим вызовом
   `write_preview`.
8. Аудит: `PatchCommitService` вызывает аудит-колбэк или пишет запись в
   `logging`; `PatchCommitResult` содержит `plan_id`, `snapshot_id`,
   `confirmed_risks` и пути. UI дополнительно выводит аудит в журнал.
   В приложении `main.py` подключает `PatchAuditLog.for_session` к
   `generated/patch-audit-<session>.log` — отдельному JSONL-файлу на
   каждую сессию рядом с `generated/crash.log`.
9. `PatchEngine` и `PatchOrchestrator` снова read-only: методы `commit` и
   `PatchCommitUnavailable` удалены. Запись — не операция формата, а
   отдельный сервис.
10. `CoreServices` создаёт `patch_commit`; `patch_writer` стал приватным,
    метод `CoreServices.write_patch` удалён. `PatchWriter` и
    `PatchWriteError` убраны из `core.__all__` и `CORE_PUBLIC_NAMES`;
    модуль `core.patching.writer` остаётся низкоуровневым примитивом для
    core-тестов.
11. `app/patching/commit.py` принимает `confirmed_risks` и не меняет
    план вручную. UI `ProjectController.write_preview` показывает
    отдельное подтверждение со списком опасных изменений и передаёт
    подтверждённые ключи в use case.
12. `PATCH_WRITE_EXPERIMENTAL` остаётся `False`; шаг 5.3 не включает
    пользовательскую запись.

## Альтернативы

1. **Оставить `plan.confirmed=True` в app.** Отклонено: это CR-1 и
   автоподтверждение без явного решения пользователя.
2. **Оставить `CoreServices.write_patch`.** Отклонено: это второй путь
   записи в обход авторизации.
3. **Разрешить `CommitAuthorization` без токена.** Отклонено: вручную
   созданный объект нельзя отличить от выпущенного сервисом.
4. **Оставить `PatchEngine.commit` как заглушку.** Отклонено: после
   появления сервиса заглушка стала бы вторым контрактом записи.
5. **Не делать post-write verification.** Отклонено: запись должна
   доказуемо совпадать с проверенным патчем.

## Последствия

- CR-1/CR-2 закрыты; TASK-2 (синхронная запись в UI) остаётся до шага
  5.5.
- Запись возможна только через `CoreServices.patch_commit` и только с
  авторизацией конкретного плана/снимка.
- `PatchWriter` остаётся низкоуровневым модулем core; прямой импорт
  `core.patching.writer` в app/UI запрещён статтестом
  `tests/test_architecture.py` вместе с ручным `confirmed=True`.
- Post-write verification и атомарный откат добавляют чтение файлов;
  это тяжёлая операция, её перенос в task service — шаг 5.5.
- Аудит хранится в `generated/patch-audit-<session>.log` (JSONL) и
  дублируется в UI-журнал; файл не является кэшем, новый файл создаётся
  на каждую сессию, старые автоматически не удаляются.
- Тесты прямого `PatchWriter` остаются unit-тестами примитива; app
  использует только commit service.

## Срок и условия выхода

- Шаг 5.5: запись через task service, прогресс и отмена; UI перестаёт
  ждать синхронно.
- Этап 9: ревизия `core.__all__` и низкоуровневого `PatchWriter`.
- Включение `PATCH_WRITE_EXPERIMENTAL` — только отдельным решением
  оператора после Этапа 8.
