# Scenario Tests (PST) — Content Strategy Hub

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

---

## Прогон 2026-08-19

**Существующее покрытие до PST:** `tests/test_smoke.py` — 2710 строк,
широкое покрытие 34 из 38 `@chat.function` вдоль всех 5 обязательных
веток (error через `result.error`/`error_code`, adversarial через
twice/duplicate/stale паттерны — 36 совпадений). Аудит по вызовам `m.xxx(`
нашёл **4 функции, никогда не вызываемые ни одним существующим тестом**:
`discover_opportunities_from_search_console`, `list_briefs`,
`update_brief_title`, `purge_site_pipeline_data` (последняя — `destructive`,
самая рискованная из четырёх).

**Новый файл:** `tests/test_pst_scenarios.py` — целенаправленно закрывает
только эти 4 функции, вдоль happy/error/blocked/recovery/adversarial:
IPC-сбой к Search Console connector (blocked, ошибка должна долетать до
пользователя, не тихо превращаться в пустой список), пустые query rows
(error, не тихий пустой успех), фильтрация `list_briefs` по сайту,
известный из докстринга баг с языком заголовка брифа (`update_brief_title`
— happy/empty/not-found), и каскадное удаление `purge_site_pipeline_data`
(not-found guard, изоляция по site_id, идемпотентность повторного вызова
на уже пустых данных).

### Результат

147/147 тестов зелёные (146 существующих + 15 новых PST, включая 1
собственную ошибку в черновике сценария, исправленную ниже). **Реальных
багов в приложении не найдено.**

Одна собственная ошибочная гипотеза в PST-сценарии, исправлена: тест
happy-path для `discover_opportunities_from_search_console` не учёл, что
делегируемая `discover_opportunities` намеренно требует существующий
content audit (`CONTENT_AUDIT_REQUIRED`) перед открытием новых
возможностей — защита от дублирования тем/каннибализации ключевых слов.
Это корректное поведение приложения, не баг; тест исправлен, чтобы сначала
засеять `content_audits`, как это уже делает существующий набор через
`_seed_audit`.

**Статус: PST пройден, код CLEAN.**
