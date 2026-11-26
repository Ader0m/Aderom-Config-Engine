# ADR-21. Preview-контрибьюторы: asset/new-file и XML-merge

Дата: 23.11.2026
Статус: accepted

## Контекст

Шаг 5.1 ввёл `PatchEngine`, `PatchOrchestrator` и четыре форматных
движка, но `app/patching/preview.py` продолжал вручную собирать
`PatchPlan`, `GeneratedPatch` и `VerificationResult` для двоичных
ресурсов, новых файлов и XML-слияния. Это CR-5 и DATA-5: второй способ
построения патча в обход движка.

Шаг 5.2 должен убрать ручную сборку, не меняя поведения предпросмотра и
не затрагивая путь записи.

## Решение

1. Предпросмотр asset использует `AssetPatchEngine`, предпросмотр
   нового файла — `NewFilePatchEngine`. Оба движка получают уже
   разрешённые данные проекта: путь ресурса, байты, режим, тип и
   `PatchSnapshot`.
2. `ProjectPreviewBuilder` создаёт локальный `PatchOrchestrator` только с
   этими двумя движками. Ему не нужен `CoreServices`: предпросмотр
   работает и при `services=None` (пустой проект, модульные тесты).
3. XML-слияние перенесено в `core/patching/xml/merge.py`:
   `merge_xml_generated(services, entries)`, где `entries` —
   `(plan, generated, source_path)`. Функция сохраняет прежнюю
   семантику: конфликт режимов, агрегацию issues/checks, фильтрацию
   `.ltx` из XML-пакета и проверку непустого набора файлов.
4. `merge_xml_generated` экспортируется через порт `core.api.patches`;
   `AssetPatchEngine` и `NewFilePatchEngine` также доступны через этот
   порт, потому что `app` создаёт их для предпросмотра.
5. `app/patching/preview.py` оставляет себе только DTO и оркестрацию:
   `PreviewItem`, заметки, чтение представления ресурса, проверку
   режима сохранения, группировку и кэш. Ручных `PatchPlan`,
   `GeneratedPatch` и `VerificationResult` в `app` больше нет.
6. `snapshot_id` леса передаётся в движки как `PatchSnapshot`; план и
   `GeneratedPatch.plan` несут тот же снимок.
7. Путь записи не меняется: `PATCH_WRITE_EXPERIMENTAL=False`,
   `write_preview` и `PatchWriter` не затрагиваются.

## Альтернативы

1. **Оставить ручные ветки в `app`.** Отклонено: это CR-5/DATA-5 и
   второй источник истины для плана.
2. **Вызывать движки напрямую, без оркестратора.** Отклонено:
   диспетчеризация по `kind` должна оставаться в одном месте.
3. **Требовать `CoreServices` для asset/new-file.** Отклонено:
   предпросмотр пустого проекта и модульные тесты не создают ядро;
   движкам ресурсов сервисы не нужны.
4. **Оставить XML-слияние в `app` как «предпросмотровую» логику.**
   Отклонено: объединение планов и проверок — часть патч-движка, а не
   UI-сценария.

## Последствия

- CR-5 и DATA-5 закрыты; `app/patching/preview.py` больше не содержит
  ручной сборки патч-моделей.
- Появился публичный порт `merge_xml_generated` и доступ к двум
  конкретным движкам через `core.api.patches`; это расширение порта, а
  не корня `core`.
- Поведение предпросмотра сохранено: существующие тесты XML, asset,
  new-file и Root-режима проходят; добавлен отдельный тест
  `tests/test_xml_patch_merge.py` для core-слияния.
- `tests/test_architecture.py::AppPatchConstructionBoundaryTest`
  статически запрещает ручную сборку `PatchPlan`/`GeneratedPatch`/
  `VerificationResult` и их компонентов в `src/app`; есть негативный
  тест на import-as и обращение к атрибуту модуля.
- Предпросмотр по-прежнему не пишет файлы и не создаёт
  `CommitAuthorization`.

## Срок и условия выхода

- Шаг 5.3: `PatchCommitService` и `CommitAuthorization`; путь записи
  переводится на движок.
- Шаг 5.5: фоновая запись и UI предпросмотра.
- Этап 9: ревизия порта `core.api.patches`.
