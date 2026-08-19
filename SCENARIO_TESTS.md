# Scenario Tests (PST) — Media Studio

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

---

## Прогон 2026-08-19

**Существующее покрытие до PST:** 308 тестов в 22 файлах — самое глубокое
покрытие среди всех приложений, пройденных в этом сквозном прогоне
(генерация, upscaling, prompt engine, model discovery, panels, pricing,
recovery-логика сопоставления). Аудит по точному имени `@chat.function`
нашёл **ровно одну** функцию, никогда не вызывавшуюся на уровне хендлера:
`recover_stored_images`. Её внутренние хелперы в `recovery.py`
(`match_creation_urls`, `list_recent_creations`) уже тестировались
напрямую в `test_recovery.py` — но сам handler (полный поток: чтение
пакетов, скачивание байт, запись в постоянное хранилище, апдейт пакета)
не был покрыт.

**Новый файл:** `tests/test_pst_scenarios.py` — 5 сценариев: blocked (нет
Magnific API key), happy no-op (нечего восстанавливать — валидный успех,
не ошибка), happy full recovery (легаси-ассет без `original_storage_path`
восстанавливается, `restored` счётчик растёт, пакет обновляется), error
(скачивание с провайдера падает — актив помечается `unavailable`, не
падает исключением), adversarial (пакет уже с `original_storage_path` —
идемпотентность: recovery не трогает уже сохранённые активы).

### Результат

313/313 тестов зелёные (308 существующих + 5 новых). **Реальных багов в
приложении не найдено.**

---
