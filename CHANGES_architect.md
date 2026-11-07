# Отчёт архитектора — кейс 2

## Исправлено
- 05_api_schemas.yaml: отступ `Decision:` (4 пробела), путь `/recalculate` (2 пробела); парсится, 3 схемы + Application, 4 path, все $ref существуют.
- 04_requirements.md: FR-01…FR-10, NFR-01…NFR-06, 3 ограничения, 2 сценария; убран «пол»; таймауты 3000 мс; enum decision; сценарий 1 приведён к шкале 0–100 (score=90).
- ИБ.md: U-6, RBAC (delete у admin только для не-аудиторских данных, аудит append-only), enum action, passport_hash вместо password_hash, маскирование income, строка про секреты.
- Порог BKI < 500: data_dictionary_sdel.md, 02_pravila_sdelannye.md, 08_reasons_sdel.md. Пример значения «550» в колонке «Пример» оставлен по инструкции.
- app.py: полный priority для MISSING_*, флаг --verbose.
- Бонус: в data_dictionary passport_hash-пример содержал не-hex символы (g,h) → исправлен; client_id пример → cli_0123 (по API-паттерну).

## Прогон (applications.csv, md5 в applications.md5)
- python app.py applications.csv --out decisions.csv → APPROVED 49 / REFUSED 50 / NEED_MORE_DATA 1; check_tests.py → 5/5.
- app.py теперь берёт smev_status из CSV (раньше игнорировал, считал по хэшу) и применяет правила в порядке эталона 07: №8 SMEV, №9 score.
- 02_pravila_sdelannye.md приведён к 10 правилам (как в 07 и app.py); SMEV_MISMATCH в 08_reasons — «Отказ» (как в CSV и эталоне).

## Решение по BKI: ЗАФИКСИРОВАНО — порог < 500 (подробно — 07_decision_mechanism.md, раздел 13)

## Расхождения с CSV (19 из 100)
1. app_0005 (HIGH_DTI в CSV vs AMOUNT_LIMIT) и app_0007 (APPROVED при MISMATCH) — известные, разобраны в 07.
2. 10 заявок с bki 500–549 (0013–0016, 0038, 0039, 0060, 0061, 0085, 0086): в CSV BKI_DELINQUENCY, т.е. разметка соответствует порогу < 550. По инструкции и эталону 07 оставлен < 500. Решение: оставляем < 500 (осознанное расхождение с разметкой CSV).
3. app_0009, 0012: в CSV отказ (AMOUNT_LIMIT/HIGH_DTI), по формулам условия не выполняются (700k < 10×80k; DTI 0.24).
4. app_0019, 0025, 0045, 0064, 0070: в CSV APPROVED при score < 60.
5. Ограничение 2 (сумма до 1M, products.csv prod_standard max 1 000 000) vs API/app.py (до 5M): продукты prod_premium допускают 5M — в API это оправдано, но Ограничение 2 говорит об одном продукте.

## Не выполнено
- Git-ветка team_N_case_2 — создать в репозитории команды.
- Онлайн-валидаторы недоступны; YAML проверен локально парсером.

## Прочие замечания
- Исправлено: ИБ.md — ротация JWT «24 часа»; 02_pravila_sdelannye.md — убраны дубли (остался один документ); 03_test_scenarios.md — T2-5 переименован в «Превышение лимита суммы (AMOUNT_LIMIT)»; чек-листы в data_dictionary и 08_reasons отмечены [x].
- Остаётся: в ИБ.md описаны угрозы U-3…U-6 (4 шт.), в критерии инструкции — 6; текста U-1, U-2 нет.
