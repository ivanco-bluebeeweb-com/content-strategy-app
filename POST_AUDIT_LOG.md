# Post-Audit Log — Content Strategy Hub

Формат и правила ведения: см. `/Users/vladivanco/Documents/Imperal OS/POST_AUDIT_LOG_STANDARD.md`.
Новые записи добавляются СВЕРХУ.

---

## 2026-08-19 — Сквозной пост-аудит + исправление double-prompt антипаттерна

**Что проверялось:** py_compile всех модулей; количество `@chat.function`
(38, совпадает с манифестом); обе `destructive`-функции (`purge_pipeline_data`,
`purge_site_pipeline_data`) на наличие ручного поля `confirm*` рядом с уже
корректным `action_type="destructive"` (доктрина Imperal: confirmation card
рендерится ТОЛЬКО по `action_type`; повторная ручная проверка в хендлере —
double-prompt антипаттерн, ломающий гарантию платформы "что видел — то и
выполнится"); отличие от легитимных `ui.Form`-паттернов (`confirm_label`,
`on_confirm=ui.Call(...)` — это компонент панели, не тот же баг, не трогалось);
полный прогон тестов (`.venv/bin/pytest tests/`, 135 тестов).

**Метод:** grep по `schemas.py`/`main.py` на `confirm` в любом виде; сверка
каждой найденной функции с её `action_type` в манифесте; правки применены к
`schemas.py`, `main.py`, синхронизированы в `imperal.json` (params_schema
очищен от убранного поля); `python3 -m py_compile`; полный `pytest` до и
после, включая обновление 4 устаревших мест в `tests/test_smoke.py`, которые
передавали уже несуществующий kwarg `confirm_wipe=True`.

### Находки

1. **`purge_pipeline_data`** — `action_type="destructive"` (корректно
   гейтит через платформенную карточку), но хендлер ДОПОЛНИТЕЛЬНО требовал
   ручное `confirm_wipe=true` и возвращал ошибку без него. Double-prompt
   баг — тот же паттерн, уже найденный и исправленный в Brand Strategy Hub
   (`delete_brand_profile`, `purge_brand_strategy_data`) в этой же сессии.
2. **`purge_site_pipeline_data`** — тот же баг с полем `confirm_wipe`.
3. Побочно: 4 тестовых вызова в `tests/test_smoke.py` передавали
   `confirm_wipe=True` в конструктор `PurgePipelineDataParams` — этот kwarg
   больше не существует после правки; тесты обновлены на `PurgePipelineDataParams()`.

### Что сделано

1. Убрано поле `confirm_wipe` из `PurgePipelineDataParams` и
   `PurgeSitePipelineDataParams` в `schemas.py`.
2. Убрана ручная проверка `if not params.confirm_wipe: return ActionResult.error(...)`
   из обоих хендлеров в `main.py`; описания функций очищены от упоминания
   `confirm_wipe=true`.
3. `imperal.json` синхронизирован: `params_schema.properties.confirm_wipe`
   удалён из обоих tool-записей.
4. 4 устаревших вызова в `tests/test_smoke.py` исправлены на новую сигнатуру.
5. Верифицировано: `py_compile` чист; полный прогон `pytest` — 135/135
   пройдено, 0 упавших (только безвредные `DeprecationWarning` из SDK-мока,
   не относящиеся к этой правке).
6. Не найдено других отклонений манифеста, нет других функций с
   `action_type` ниже необходимого уровня безвозвратности среди 38
   зарегистрированных инструментов.

**Статус: FIXED.**
