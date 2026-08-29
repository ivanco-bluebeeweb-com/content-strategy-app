# Content Strategy Hub — UI component plan

Источники: `Docs/session-notes/UI_COMPONENT_VOCABULARY.md`, `UI_INTERFACE_STANDARD.md`,
`concepts/panels.md`. Основано на функционале `content-strategy-app` (аудит контента,
опоры/брифы, редакционный календарь — без внешнего `connect_*`, но с зависимостью от
Google Search Console Connector и WordPress Hub).

## 0. Разница с реализацией сейчас
Нет формы подключения аккаунта — первое действие это регистрация сайта. Стоит
выровнять по стандарту:
- Первый экран — прямая форма `create_site_profile`, не generic `Empty`.
- Честно показать зависимость от Search Console (баннер, не тихая пустота).
- `run_content_audit` явно обозначен как обязательный первый шаг после регистрации
  сайта, не один из равнозначных пунктов меню.

## 1. Компоненты

| Экран | Примитивы | Почему именно эти |
|---|---|---|
| Sidebar (left) | `ui.Column`(align="start") + `ui.Divider` + navigation `ui.ListItem`(Sites/Queue/Calendar/Link Building) + `ui.Button`("App settings") | Без карточек, по стандарту. |
| Empty (нет сайтов) | `ui.EmptyState`(title="Зарегистрируйте сайт", body, `ui.Button`("+ Зарегистрировать сайт") → Site Profile Form) | Прямой CTA. |
| Site Profile Form | `ui.Form`(action="create_site_profile") + `ui.Input`(label="Домен сайта", placeholder="example.com") + `ui.Input`(label="Название бренда", placeholder="напр. Acme Coffee") + `ui.Select`(label="Языки контента", multi) + `ui.TextArea`(label="Описание бизнеса", placeholder="Чем занимается сайт, для кого") | Все поля с лейблами и контекстными плейсхолдерами. |
| GSC Dependency Banner | `ui.Alert`(info, "Подключите Google Search Console, чтобы получать реальные поисковые запросы для контент-идей" + `ui.Button`("Подключить")) | Честная зависимость, не молчаливая пустота при вызове функции. |
| Mandatory Audit CTA | `ui.Alert`(warning, "Сначала запустите аудит существующего контента" + `ui.Button`("Запустить аудит")) | `run_content_audit` явно обязателен перед стратегией. |
| Audit Result | `ui.Stats`(pages/decaying/cannibalized) + `ui.DataTable`(findings; sortable) | Сводка технического состояния контента сайта. |
| Opportunity/Queue List | `ui.DataTable`(title, status Badge idea/brief_ready/draft_requested/published; sortable) + `ui.Button`("+ Сгенерировать идеи") | Редакционная очередь с прогрессом по стадиям. |
| Brief Detail | `ui.KeyValue`(keyword/summary) + `ui.Button`("Отправить в Article Writer") | Передача брифа во внешний писательский модуль. |
| Content Calendar | `ui.Calendar`(scheduled_date × queue items) | Визуальный график публикаций. |
| Link Building List | `ui.DataTable`(target domain, status Badge prospected/contacted/replied; sortable) | Пайплайн аутрича. |
| App Settings | `ui.Accordion`([Sites, Purge pipeline data]) | Централизованные настройки по стандарту. |

## 2. User flow (валидно по panel lifecycle)

1. **SESSION INIT, нет сайтов** → Empty с CTA "+ Зарегистрировать сайт" → Site
   Profile Form → `create_site_profile` → редирект на профиль сайта.
2. Сразу после регистрации → `Alert`(warning) Mandatory Audit CTA ПОВЕРХ обычного
   меню, пока аудит не запущен хотя бы раз → `run_content_audit`.
3. Если Search Console не подключён и пользователь пытается сгенерировать идеи из
   поисковых данных → `Alert`(info) GSC Dependency Banner вместо тихой пустоты.
4. После аудита → Audit Result → CTA "Сгенерировать контент-идеи" →
   `generate_strategic_topics`/`discover_opportunities` → Opportunity List.
5. Opportunity List → клик → `create_brief` → Brief Detail → "Отправить в Article
   Writer" → `build_writer_brief`.
6. Queue → Content Calendar — визуальное распределение публикаций по датам.
7. Link Building — отдельный раздел sidebar, независимый пайплайн аутрича.
8. App Settings — доступен из sidebar в любой момент.

## 3. Экраны/карточки (конкретно для этого приложения)

- **Screen: Empty + CTA** — EmptyState + Button.
- **Screen: Site Profile Form** — Form(4 поля, растянута на ширину контейнера).
- **Screen: Audit Result** — Stats(3) + DataTable(3 колонки).
- **Screen: Opportunity/Queue List** — DataTable(2 колонки) + Button.
- **Screen: Brief Detail** — KeyValue + Button.
- **Screen: Content Calendar** — Calendar.
- **Screen: Link Building List** — DataTable(2 колонки).
- **Screen: App Settings** — Accordion(2 секции).

Ограничение SDK, учтённое в плане: межприложенческие переходы (Search Console,
WordPress Hub, Article Writer, Media Studio) реализованы через явные ссылки/кнопки,
а не встроенный iframe — в текущем инвентаре нет примитива встраивания стороннего
приложения внутрь панели.
