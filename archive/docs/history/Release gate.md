# Release gate Этапа 8.6

Дата: 26.11.2026
Статус: **8.6 выполнен в экспериментальном режиме**; 8.3 остаётся
задокументированным открытым отличием.
Решение: `docs/adr/ADR-37-release-gate-write.md`.

## 1. Что включено

- `PATCH_WRITE_EXPERIMENTAL=True` — пользовательская запись патчей
  включена.
- Статус в главном окне: «Запись патчей: экспериментальная»; подсказка
  сообщает, что паритет `19023/19024` не подтверждён.
- Откат: вернуть `PATCH_WRITE_EXPERIMENTAL=False`; данные проекта и
  формат патчей не мигрируются.
- Путь записи не менялся: только `PatchCommitService` +
  `CommitAuthorization`, полный round-trip до авторизации, без
  автоподтверждения и без прямого `PatchWriter` из `app`/`ui`.

## 2. Чек-лист

| Проверка | Команда / тест | Результат |
|---|---|---|
| Все тесты | `python -m pytest -q` | **1370 passed, 1 skipped, 76 subtests** |
| Компиляция | `python -m compileall -q src` | OK |
| Пробелы/формат | `git diff --check` | OK |
| Архитектурные границы | `tests/test_architecture.py` | OK |
| Эталонный patch round-trip | `tests/test_release_gate_reference.py` | OK |
| Авторизация и commit | `tests/test_patch_commit_service.py` | OK |
| Полный round-trip | `tests/test_patch_roundtrip.py` | OK |
| Фоновая запись/отмена | `tests/test_patch_write_async.py` | OK |
| Семантика с манифестом | `tools/semantic_parity.py --manifest docs\baseline\stage0-manifest.json` | 11870/11870 секций, 0 отличий |
| Нагрузочный VFS | `tools/load_check.py --sections 19000 --files 20000 --workers 4` | `ok=true`, 20000 файлов, 19002 секции, 999 корней, отмена работает |
| Производительность | `tools/perf_baseline.py --skip-ui` | `tree_build` +9.7…+13.9% (снимок ~122 мс, `tree_build_raw` +2.1…+6.0%), остальные метрики ≤ +7.9% от baseline; разброс объяснён |
| Полная композиция | offscreen `main.py` | старт, загрузка модулей, закрытие во время `vfs.rebuild` без падения и без диалога ошибки |

Полный прогон после изменений: **1370 passed, 1 skipped, 76 subtests**.

## 3. Что не подтверждено

- **8.3 (19023/19024) не закрыт.** Игрового `game_sections.txt` /
  `game_properties.txt` в репозитории нет; `semantic_parity --release-gate`
  нельзя получить зелёным. Отличие остаётся открытым и не считается
  подтверждённым паритетом.
- Поэтому статус записи — **экспериментальная**, а не боевая. Для
  перехода в боевой режим нужен игровой дамп, зелёный
  `--release-gate`, затем отдельное обновление ADR-37 и статуса UI.
- Нагрузочный VFS — синтетический smoke-check; по документации он
  использует `FileCacheSystem`/`ResolvedCache` напрямую и не заменяет
  production-профиль полной композиции.
- `tests/main_unit_test_patch.py` был адаптирован к `CoreComposition` в
  коммите `676e20c`, а в 9.4 переведён на `CacheService`; ожидаемые
  значения теста не менялись. Изменение выполнено по явному указанию
  оператора от 26.11.2026 (`AGENTS.md` §4.7).

## 4. Откат

1. Вернуть `PATCH_WRITE_EXPERIMENTAL=False` в
   `src/ui/platform/feature_flags.py`.
2. Прогнать `tests/test_feature_flags.py` и
   `tests/test_patch_write_async.py`.
3. Обновить `docs/Текущий режим.md`: запись снова «выключена».
4. Патчи, уже записанные пользователем, не удаляются и не мигрируют.
