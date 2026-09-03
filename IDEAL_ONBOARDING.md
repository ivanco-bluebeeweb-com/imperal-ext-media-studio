# Media Studio — идеальный первый запуск

Источник: `ONBOARDING_FIRST_LAUNCH_STANDARD.md`. Целевой пользователь: контент/SEO-
менеджер, которому нужны AI-изображения для статей (Magnific/Mystic под капотом).

## 1. Credential type
API key (Magnific, BYOK — Bring Your Own Key, одно поле).

## 2. Идеальный флоу
1. **Первое открытие** — `Empty` со ссылкой на получение Magnific API key + честное
   объяснение "это отдельный сервис от Imperal — вам нужен собственный ключ, работа
   тарифицируется на его стороне".
2. **Форма** — api_key (password-type) с лейблом.
3. **После успеха** — сразу форма создания первого media brief (`create_project` +
   `create_media_brief`) как основной CTA в центре — не пустой экран "подключено".
4. **Cross-app awareness** — если у пользователя уже есть проекты в Content Strategy
   Hub — идеально: предложить сразу привязать Media Studio к существующему сайту/
   проекту, не создавать дубликат структуры вручную.
5. **Ошибка "insufficient balance/quota"** — Magnific API может иметь квоты/баланс —
   конкретное сообщение с прямой ссылкой на пополнение на стороне Magnific.

## 3. Разница с реализацией сейчас
См. `UI_COMPONENT_PLAN.md` §0.
