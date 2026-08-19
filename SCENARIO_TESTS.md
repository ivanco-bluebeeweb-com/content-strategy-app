# Scenario Tests (PST) — Content Strategy Hub

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

---

## Прогон 2026-08-20 — Часть D (Deploy Verification / Idempotency / Security-SSRF / Regression grep)

**D1 (Deploy Verification):** не применялось — код приложения не менялся (только тесты), деплой не требуется.

**D2 (Idempotency):** `purge_site_pipeline_data` уже имел собственный adversarial-тест на идемпотентность из предыдущего прогона. Добавлен недостающий тест на портфельную версию `purge_pipeline_data` (без site_id) — второй вызов подряд находит все коллекции уже пустыми и возвращает нулевые счётчики без ошибки.

**D3 (Security/SSRF):** множество URL-подобных полей (`add_site_competitor.url`, `recommended_target_url`, `external_link_url`, и т.д.), но grep по `main.py` подтвердил отсутствие любого `ctx.http`/`httpx`/`requests`/`urlopen` вызова — все они хранятся как данные, не фетчатся. Добавлен 1 regression-тест: adversarial-значение `http://169.254.169.254/latest/meta-data/` подаётся как `url` в `add_site_competitor`, подтверждается, что оно сохраняется как есть.

**D4 (Regression grep):** нет новых находок специфичных для этого приложения сверх `Docs/known-bug-patterns.md`.

**Итог:** 149/149 тестов зелёные (было 145). Реальных багов не найдено.

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
