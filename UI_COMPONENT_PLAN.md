# Media Studio — UI component plan

Источники: `Docs/session-notes/UI_COMPONENT_VOCABULARY.md`, `UI_INTERFACE_STANDARD.md`,
`concepts/panels.md`. Основано на функционале `media-studio` (Magnific/Mystic BYOK).

## 0. Разница с реализацией сейчас
Приложение уже реализует форму подключения (`ConnectMagnificParams` — один api_key)
и рабочий процесс brief→package→assets. Что стоит выровнять по стандарту:
- Sidebar не должен дублировать инструкции, которые уже есть в модалке подключения
  (кнопка+модалка — единственное место с объяснением, откуда взять API key).
- Первый экран без ключа — `Empty` с явным CTA "Подключить Magnific", не пустая
  таблица пакетов.
- После подключения — прямой переход в форму создания первого брифа, а не в общий
  список (изначально пустой).

## 1. Компоненты

| Экран | Примитивы | Почему именно эти |
|---|---|---|
| Sidebar (left) | `ui.Column`(align="start") + `ui.Text`(providers status) + `ui.Divider` + navigation `ui.ListItem`(Projects/Media Packages/Prompt Engine log) + `ui.Button`("App settings") | Без карточек, без дублирования инструкции подключения. |
| Empty (no key) | `ui.EmptyState`(title="Подключите Magnific", body, `ui.Button`("Подключить") → `ui.Dialog`(Form: api_key password-input с лейблом + ссылка)) | Кнопка+модалка — единственное место с инструкцией. |
| Project List | `ui.DataTable`(name, sites count, briefs count; sortable) + `ui.Button`("+ Новый проект") | Обзор проектов с прямым созданием нового. |
| Brief Form | `ui.Form`(action="create_media_brief") + `ui.Select`(project) + `ui.Input`(label="Заголовок статьи", placeholder="Название вашей статьи") + `ui.Input`(label="Краткое summary", placeholder="О чём статья в двух предложениях") + `ui.Input`(type="number", label="Количество inline-изображений", placeholder="напр. 3") | Форма растянута на всю ширину сайдбара/центра, лейблы обязательны. |
| Media Package Detail | Back-button + `ui.KeyValue`(project/site/status) + `ui.Grid`(N×`ui.Card`(image + Badge status pending/done/failed) — единственное разрешённое использование Card, т.к. это визуальная галерея, не список настроек) + `ui.Row`(Button "Regenerate", "Upscale") | Галерея изображений естественно card-based (превью), не нарушает правило "без карточек в сайдбаре". |
| Model Discovery Log | `ui.Timeline`(дата проверки → найдено/не найдено новых моделей) | История автоматических проверок новых моделей Magnific. |
| Prompt Engine Settings | `ui.Accordion`([Generic lighting/camera defaults, Self-review log]) | Настройки движка промптов, отдельно от рабочего процесса brief. |
| App Settings | `ui.Accordion`([Connection+Disconnect, Model discovery schedule]) | Централизованные настройки по стандарту. |

## 2. User flow (валидно по panel lifecycle)

1. **SESSION INIT, нет ключа** → Sidebar только с `ui.Button`("App settings"), центр —
   `Empty` с CTA "Подключить Magnific" → Dialog(Form api_key) → `connect_magnific` →
   `refresh_panels`.
2. **После подключения** → редирект в Project List; если проектов 0 — Empty с CTA
   "+ Новый проект" вместо пустой таблицы.
3. Project List → "+ Новый проект" → `create_project` → сразу форма Brief.
4. Brief Form → `create_media_brief` → `generate_media_package` (может быть отдельной
   кнопкой "Сгенерировать изображения" на брифе, не автоматически, т.к. тратит баланс).
5. Media Package Detail: по завершении генерации карточки обновляются через polling/
   `refresh_panels`; "Regenerate"/"Upscale" — по одному ассету за раз, с `ui.Dialog`
   подтверждением (тратит баланс на стороне Magnific).
6. Model Discovery Log и Prompt Engine Settings — доступны из sidebar, read-only
   разделы с историей автоматических проверок.
7. App Settings — доступен из sidebar в любой момент.

## 3. Экраны/карточки (конкретно для этого приложения)

- **Screen: Empty (no key)** — EmptyState + Button→Dialog(Form 1 поле).
- **Screen: Project List** — DataTable(3 колонки) + Button("+ Новый проект").
- **Screen: Brief Form** — Form(4 поля, все с лейблами, растянута на ширину контейнера).
- **Screen: Media Package Detail** — KeyValue + Grid(N×Card изображений) + Row(2 Button).
- **Screen: Model Discovery Log** — Timeline.
- **Screen: Prompt Engine Settings** — Accordion(2 секции).
- **Screen: App Settings** — Accordion(2 секции).

Ограничение SDK, учтённое в плане: генерация асинхронная (Magnific job), поэтому
Media Package Detail не блокирует UI — статус обновляется по `refresh_panels`/поллингу,
нет отдельного WebSocket-примитива в текущем инвентаре.
