# Post-Audit Log — Media Studio

Формат и правила ведения: см. `/Users/vladivanco/Documents/Imperal OS/POST_AUDIT_LOG_STANDARD.md`.
Новые записи добавляются СВЕРХУ.

---

## 2026-08-19 — Plausible Scenario Testing (PST) — 1 непокрытая функция закрыта

Полный метод и детали — в `SCENARIO_TESTS.md` этого приложения. Кратко:
из 24 функций и 308 существующих тестов только `recover_stored_images`
никогда не вызывалась на уровне хендлера — закрыто 5 новыми тестами в
`tests/test_pst_scenarios.py` (blocked/happy no-op/happy full
recovery/error/adversarial-idempotent). Полный набор (313 тестов) зелёный.
Реальных багов не найдено.

---

## 2026-08-19 — Сквозной пост-аудит + исправление неверной классификации action_type

**Что проверялось:** py_compile всех 17 модулей; количество `@chat.function`
(24, совпадает с манифестом); каждая `delete_*`/`purge_*` функция и её
`action_type` (доктрина Imperal: confirmation card рендерится ТОЛЬКО по
`action_type=\"destructive\"` — если необратимая функция помечена `write`,
карточка подтверждения вообще не показывается перед удалением); double-prompt
антипаттерн (ручной `confirm*` рядом с уже корректным `destructive`); полный
прогон всех 21 тестовых файлов (308 тестов, .venv/bin/pytest, по отдельным
файлам/пачкам — один файл, `test_handlers.py`, реально медленный: 62с/42 теста,
не hang, вероятно живые сетевые вызовы к докам моделей).

**Метод:** `python3 -c` через `json` — прошлась по всем `tools[].action_type`
в `imperal.json`, отфильтровала по подстроке `delete`; нашла ДВЕ функции с
именами `delete_asset_image`/`delete_media_package`, но `action_type=\"write\"`
— проверила их docstring/description (\"Permanently delete...\",
\"Irreversible\" по смыслу) — обе реально необратимые операции. Прочитала
точный код декоратора в `handlers.py`, схемы параметров в `models.py`
(ни у одной нет ручного `confirm*` поля — значит это НЕ double-prompt, а
чистая ошибка выбора `action_type` при написании функции). grep по
`confirm` во всех остальных модулях — все совпадения оказались безвредным
текстом в docstring/комментариях (\"...confirmed via docs...\",
\"...same way every model was confirmed...\") или легитимными UI-строками
панели (`\"confirm\": f\"Delete media package...\"`), не повторным серверным
гейтом.

### Находки

1. **`delete_asset_image`** — удаляет навсегда одну версию (`original`/
   `upscaled`) хранимого изображения из ассета. Был `action_type=\"write\"`.
   Это баг: пользователь мог случайно (например через неверно понятую
   команду) стереть безвозвратно сохранённое изображение БЕЗ единой
   платформенной карточки подтверждения — ни ручного confirm-поля, ни
   `destructive`-гейта.
2. **`delete_media_package`** — тот же баг: \"Permanently delete a media
   package and all of its asset records\" был `action_type=\"write\"`.
   Тот же риск: полное необратимое удаление пакета без единого
   подтверждения.
3. Double-prompt антипаттерн (ручное поле `confirm*` при уже корректном
   `destructive`) НЕ найден нигде в приложении — единственная проблема была
   обратная: отсутствие `destructive` вообще, а не его дублирование.

### Что сделано

- `handlers.py`: `action_type` изменён с `\"write\"` на `\"destructive\"` для
  обеих функций; добавлено слово \"Irreversible.\" в описание
  `delete_asset_image` (у `delete_media_package` уже было \"Permanently\" в
  описании — оставлено как есть).
- `imperal.json`: синхронизировано программно — `action_type` для обеих
  функций теперь `\"destructive\"`.
- `python3 -m py_compile` — чисто.
- Полный прогон всех 21 тестовых файлов (308 тестов) — все прошли и до, и
  после правки (правка `action_type` не меняет поведение самого хендлера,
  только платформенный гейт — тесты, вызывающие хендлер напрямую в моках,
  не видят разницы; сам платформенный confirmation-card рендерится вне
  зоны видимости unit-тестов).

**Статус: ИСПРАВЛЕНО.** 2 функции переведены на корректный `action_type`.
