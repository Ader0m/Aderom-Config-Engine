# ADR-20. Контракт патч-движка и переименование SDK-запроса

Дата: 23.11.2026
Статус: accepted

Примечание: часть решения о `PatchEngine.commit` и
`PatchCommitUnavailable` заменена ADR-22; движки остаются read-only, а
запись выполняет `PatchCommitService`.

## Контекст

Этап 5 требует один конвейер для LTX, XML, двоичных ресурсов и новых
файлов: намерение → план → сгенерированный патч → проверка →
авторизация → фиксация. Шаг 5.1 вводит только контракт и форматные
движки; путь записи не меняется, `PATCH_WRITE_EXPERIMENTAL` остаётся
`False`.

В SDK уже есть `PatchIntent` — запрос модуля на предложение изменения.
Целевая архитектура приложения (§4.8) называет `PatchIntent` ядровое
семантическое намерение. Два одинаковых имени в разных слоях создают
неоднозначность. Решением оператора SDK-контракт переименован в
`PatchRequest`.

`AGENTS.md` §4.3 требует ADR для нового публичного API и запрещает
shim/legacy-алиасы до `1.0`. ADR-11 разрешает breaking changes до `1.0`
при наличии ADR и версии.

## Решение

1. Ядровый контракт живёт в `core.patching`:
   - `PatchIntent` — семантическое намерение;
   - `PatchSnapshot` — контекст снимка (`forest`, `overlay`,
     `snapshot_id`);
   - `PatchBundle` — намерение, снимок, план, патч, проверка, заметки;
   - `PatchEngine` — `plan`/`generate`/`verify`/`preview`/`commit`;
   - `PatchOrchestrator` — выбор движка по `intent.kind`.
2. Реализации: `LtxPatchEngine`, `XmlPatchEngine`, `AssetPatchEngine`,
   `NewFilePatchEngine`. LTX/XML — тонкие адаптеры над существующими
   `LtxPatchService`/`XmlPatchService`; asset/new-file собирают
   `PatchPlan`/`GeneratedPatch` без доменных моделей `app`.
3. `PatchEngine.commit` объявлен, но до шага 5.3 поднимает
   `PatchCommitUnavailable`: единственная точка записи —
   `PatchCommitService`, который появится на шаге 5.3. Это явная
   стадия контракта, а не fallback и не второй путь записи.
4. Новые имена не добавляются в `core.__all__`. Прикладной слой получает
   их через порт `core.api.patches`; `CoreServices.patch_orchestrator` —
   точка композиции. Широкая поверхность `core` (SDK-3) не расширяется,
   `tests/architecture_allowlist.py` по `CORE_PUBLIC_NAMES` не меняется.
5. В SDK:
   - `PatchIntent` → `PatchRequest`;
   - `PatchApi.propose(request: PatchRequest)`;
   - `SDK_VERSION` → `0.5.0`;
   - совместимого алиаса `PatchIntent` нет.
6. `PATCH_WRITE_EXPERIMENTAL` остаётся `False`. Шаг 5.1 не добавляет
   запись, не меняет `write_preview` и не включает подтверждение.
7. `PatchBundle` — pre-authorization результат движка: намерение,
   снимок, план, патч, проверка и заметки. `CommitAuthorization` в него
   не входит: он выпускается отдельно `PatchCommitService.authorize`
   после подтверждения рисков и полного round-trip (ADR-22), а
   потребляется `PatchCommitService.commit`. Вручную создавать
   авторизацию нельзя.
8. Источники истины параметров:
   - `PatchSnapshot.snapshot_id` — главный источник, если не пуст:
     LTX/XML переносят его в `PatchOptions.snapshot_id`, а planner — в
     `PatchPlan.snapshot_id`; asset/new-file пишут его в
     `PatchPlan.snapshot_id` напрямую. В обоих случаях
     `GeneratedPatch.plan.snapshot_id` совпадает с планом. Если снимок
     пуст, используется непустой `PatchOptions.snapshot_id` — fallback
     одинаков для всех четырёх движков. Непустой несовпадающий
     `PatchOptions.snapshot_id` — ошибка `PatchPlanningError`, а не
     молчаливая подмена;
   - `PatchIntent.patch_mod` — уточнение на уровне намерения: непустое
     значение переопределяет `PatchOptions.patch_mod`; пустое — берётся
     `PatchOptions.patch_mod`, затем значение по умолчанию. Для
     asset/new_file это имя патч-мода;
   - `PatchIntent.asset_label` не вводится: движку двоичного ресурса
     метка не нужна, человекочитаемые notes строит `app`.
9. `PatchBundle.ok` требует отсутствия ошибок плана, непустого набора
   файлов и успешной проверки; одного `verification.ok` недостаточно.

## Альтернативы

1. **Оставить SDK `PatchIntent`, ядровый назвать `PatchRequest`.**
   Отклонено: целевая архитектура приложения (§4.8) фиксирует
   `PatchIntent` как ядровое намерение, а оператор выбрал переименование
   SDK.
2. **Оставить алиас `PatchIntent = PatchRequest` в SDK.**
   Отклонено: `AGENTS.md` §4.3 запрещает shim и два имени для одного
   действия до `1.0`.
3. **Добавить `PatchEngine` и `PatchBundle` в `core.__all__`.**
   Отклонено: это расширяет и без того широкую поверхность `core` до
   Этапа 9 и требует расширения allowlist без пользы для слоёв.
4. **Реализовать `commit` уже на шаге 5.1.**
   Отклонено: авторизация и единая точка записи — шаг 5.3; половинчатый
   путь записи нарушил бы правило «ни одной записи без
   `CommitAuthorization`».
5. **Не вводить asset/new-file движки до 5.2.**
   Отклонено: план Этапа 5 требует единый контракт для всех четырёх
   видов; движки вводятся сейчас, перенос веток предпросмотра — шаг 5.2.

## Последствия

- Появляется новый ядровый контракт и четыре форматных движка; поведение
  существующего предпросмотра не меняется, `app/patching/preview.py`
  остаётся на прежнем пути до шага 5.2.
- `PatchCommitUnavailable` — временное состояние до 5.3. Тест
  `tests/test_patch_engine.py` фиксирует, что запись через движок
  закрыта.
- `AssetPatchEngine.generate(plan)` явно запрещён: байты ресурса не
  хранятся в `PatchPlan`, патч собирается вместе с планом. Это
  ограничение контракта, а не скрытый путь записи.
- SDK-потребители обязаны использовать `PatchRequest`; в репозитории это
  затрагивает только `PatchApiAdapter` и тест SDK.
- Расширение публичного API ядра оформлено портом `core.api.patches`, а
  не корнем `core`.
- Тесты `tests/test_patch_engine.py` проверяют перенос `snapshot_id` во
  всех четырёх движках, приоритет `patch_mod` и отказ при несовпадении
  снимков.

## Срок и условия выхода

- Шаг 5.2: `app/patching/preview.py` переводится на движки; ручные
  ветки asset/new-file удаляются.
- Шаг 5.3: вводится `PatchCommitService` и `CommitAuthorization`;
  `PatchCommitUnavailable` и тест на недоступность удаляются.
- Этап 9: ревизия поверхности `core` и порта `core.api.patches`.
- Пересмотр SDK-переименования — только до `1.0` и только с новым ADR;
  алиасы не добавляются.
