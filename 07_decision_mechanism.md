# Механизм формирования предварительного решения — кейс 2 (ЭТАЛОН)

## Версия и статус

* Версия: 1.0 (эталон для команды)
* Автор: организатор кейс-чемпионата
* Связанные файлы: `02_scoring_rules.md`, `03_data_dictionary.md`, `06_external_services.md`, `08_reasons.md`, `09_tests.md`
* Назначение: показать команде, как выглядит правильно заполненный файл. Команда **сверяет** со своим решением, а не копирует.

---

## 1. Общая логика

Конвейер проходит 8 шагов. Каждый шаг — либо «продолжить», либо «вернуть решение». Порядок не случаен: сначала дешёвые проверки (валидация), потом внешние (СМЭВ, БКИ), потом расчёты (скоринг, лимиты), потом фиксация.

Схема:

```

Вход: заявка (app_id, age, income, employment, family, amount, term)



   ┌──────────────┐

   │ 1. Валидация │──ошибка──► 422 / NEED_MORE_DATA

   └──────┬───────┘

          │ ok

   ┌──────▼───────┐

   │ 2. СМЭВ      │──MISMATCH──► REFUSED (SMEV_MISMATCH)

   └──────┬───────┘──timeout───► NEED_MORE_DATA (EXTERNAL_TIMEOUT)

          │ MATCH

   ┌──────▼───────┐

   │ 3. БКИ       │──timeout───► NEED_MORE_DATA (EXTERNAL_TIMEOUT)

   └──────┬───────┘

          │ score, delinquency

   ┌──────▼───────┐

   │ 4. Скоринг   │

   └──────┬───────┘

          │ score

   ┌──────▼───────┐

   │ 5. Лимиты    │──превышение──► REFUSED (AMOUNT_LIMIT) или снижение

   └──────┬───────┘

          │

   ┌──────▼───────┐

   │ 6. Правила   │──сработало──► REFUSED (reason)

   │    отказа    │

   └──────┬───────┘

          │ все пройдены

   ┌──────▼───────┐

   │ 7. Решение   │──► APPROVED + параметры

   └──────┬───────┘

          │

   ┌──────▼───────┐

   │ 8. Аудит     │──► запись в AuditEvent

   └──────────────┘

```

---

## 2. Шаги (детально)

### Шаг 1. Валидация входных данных

Что проверяем:

* обязательные поля: `age`, `income`, `employment`, `family`, `amount`, `term`;
* `age` — целое, 18–70;
* `income` — число, > 0;
* `employment` — одно из 5 значений;
* `family` — одно из 3 значений;
* `amount` — число, 10 000–5 000 000;
* `term` — целое, 3–60;
* `passport_hash` — 16 hex-символов (если передан).

Если ошибка:

* вернуть `422 VALIDATION_ERROR` с указанием полей;
* если поле просто пустое (не неверное) — вернуть `NEED_MORE_DATA` с `reason=MISSING_*` (например, `MISSING_INCOME`).

Почему первым: валидация не требует внешних вызовов, отсеивает мусор до трат на БКИ/СМЭВ.

---

### Шаг 2. Проверка клиента по СМЭВ

Что делаем:

* `POST /smev/identity` с `{client_id, passport_hash}`;
* ждём ответ ≤ 3 с;
* один retry через 0.5 с.

Если MISMATCH:

* вернуть `REFUSED`, `reason=SMEV_MISMATCH`;
* в аудит записать `service=SMEV, status=MISMATCH`.

Если timeout:

* вернуть `NEED_MORE_DATA`, `reason=EXTERNAL_TIMEOUT`;
* **не** одобрять автоматически.

Почему вторым: проверка личности — предпосылка для любого решения. Без MATCH нет смысла считать скоринг.

---

### Шаг 3. Запрос в БКИ

Что делаем:

* `POST /bki/score` с `{passport_hash}`;
* ждём ответ ≤ 3 с;
* один retry через 0.5 с;
* получаем `{score, delinquency, inquiries_90d}`.

Если timeout:

* вернуть `NEED_MORE_DATA`, `reason=EXTERNAL_TIMEOUT`;
* **не** одобрять автоматически.

Если delinquency=true:

* это сразу триггер для правила отказа на шаге 6 (`BKI_DELINQUENCY`), но не отказ здесь — шаг 6 решает.

Почему третьим: кредитная история — второй по важности внешний источник, но дороже СМЭВ по времени.

---

### Шаг 4. Скоринг

Что делаем: считаем сумму баллов по 5 признакам (см. `02_scoring_rules.md`):

|Признак|Формула / правило|Макс. балл|
|-|-|-|
|Доход|≥150k→30; 80–150k→20; 40–80k→10; <40k→0|30|
|Занятость|employed→25; self-employed→20; retired→10; student→5; unemployed→0|25|
|Возраст|25–50→20; 18–24 или 51–60→10; 61–70→5|20|
|Семья|married→15; children→15; single→10|15|
|БКИ|≥700→10; 600–699→5; 500–599→0; <500 или delinquency→−20|10|

Итог: `score = s_income + s_employment + s_age + s_family + s_bki`.

Максимум = 100, минимум = 0 (но при delinquency может быть <0 — тогда фиксируем 0).

Формула:

```

score = 30·[income≥150k] + 20·[80k≤income<150k] + 10·[40k≤income<80k]

      + 25·[employment=employed] + 20·[self-employed] + 10·[retired] + 5·[student]

      + 20·[25≤age≤50] + 10·[18≤age≤24 or 51≤age≤60] + 5·[61≤age≤70]

      + 15·[family in (married, children)] + 10·[family=single]

      + 10·[bki≥700] + 5·[600≤bki<700] + 0·[500≤bki<600] + (−20)·[bki<500 or delinquency]

```

Если score < 0: фиксируем 0 и записываем в аудит `score_capped_to_zero=true`.

---

### Шаг 5. Проверка лимитов

Что делаем:

1. **DTI (долговая нагрузка).**

```

monthly_rate = annual_rate / 12 = 0.18 / 12 = 0.015

payment = amount × monthly_rate / (1 − (1 + monthly_rate)^(−term))

dti = payment / income

```

Порог: `dti ≤ 0.5`.

2. **Сумма к доходу.**

```

max_amount = 10 × income

```

Если `amount > max_amount` — либо отказ (`AMOUNT_LIMIT`), либо снижение до `max_amount` (решение команды, обосновать в `02`).

3. **Параметры продукта** (из `products.csv`):
* `amount` в диапазоне `[min_amount, max_amount]`;
* `term` в диапазоне `[min_term, max_term]`;
* `age` в диапазоне `[min_age, max_age]`;
* `income ≥ min_income`.

Если превышение:

* `REFUSED`, `reason=AMOUNT_LIMIT` (или `TERM_OUT_OF_RANGE`);
* либо снижение суммы с записью `amount_approved = max_amount`.

Почему после скоринга: лимиты — это «второй фильтр», они ужесточают решение, но не отменяют скоринг. Если клиент прошёл скоринг, но не проходит по лимиту — можно предложить меньшую сумму.

---

### Шаг 6. Применение правил отказа

Порядок (сверху вниз, первое сработавшее — финальное):

|№|Условие|decision|reason|
|-|-|-|-|
|1|`age < 18` или `age > 70`|REFUSED|AGE_OUT_OF_RANGE|
|2|`employment = unemployed`|REFUSED|INVALID_EMPLOYMENT|
|3|`income` пуст|NEED_MORE_DATA|MISSING_INCOME|
|4|`income < 30000`|REFUSED|LOW_INCOME|
|5|`dti > 0.5`|REFUSED|HIGH_DTI|
|6|`bki_score < 500` или `delinquency = true`|REFUSED|BKI_DELINQUENCY|
|7|`amount > 10 × income`|REFUSED|AMOUNT_LIMIT|
|8|`smev = MISMATCH`|REFUSED|SMEV_MISMATCH|
|9|`score < 60`|REFUSED|LOW_SCORE|
|10|всё пройдено|APPROVED|—|

Если сработало: фиксируем `decision`, `reason`, пишем в аудит, возвращаем клиенту с человеческим текстом (см. `08_reasons.md`).

Почему такой порядок:

* сначала структурные проверки (возраст, занятость) — они не требуют расчётов;
* потом доход и DTI — базовые финансовые;
* потом БКИ — кредитная история;
* потом лимиты — они уже учитывают доход;
* потом СМЭВ — редкий случай, но важный;
* потом score — агрегат, он последний, потому что включает всё.

---

### Шаг 7. Формирование решения

Что делаем:

* если `APPROVED`:

  * `amount_approved = amount` (или сниженный до `max_amount`);
  * `term_approved = term`;
  * `rate = product.rate` (0.18 для `prod_standard`);
  * `reason = ""`;
* если `REFUSED`:

  * `amount_approved = null`;
  * `term_approved = null`;
  * `rate = null`;
  * `reason = <код из шага 6>`;
* если `NEED_MORE_DATA`:

  * `reason = MISSING_*` или `EXTERNAL_TIMEOUT`;
  * клиенту показываем «укажите доход» / «попробуйте позже».

Что возвращаем:

```json

{

  "app_id": "app_0001",

  "decision": "APPROVED",

  "reason": "",

  "score": 75,

  "dti": 0.21,

  "amount_approved": 500000,

  "term_approved": 24,

  "rate": 0.18,

  "bki": {"score": 720, "delinquency": false},

  "smev": "MATCH"

}

```

---

### Шаг 8. Запись в аудит

Что пишем (AuditEvent):

|Поле|Пример|Зачем|
|-|-|-|
|`event_id`|evt_0001|уникальный ID события|
|`app_id`|app_0001|привязка к заявке|
|`step`|scoring|на каком шаге|
|`decision`|APPROVED|промежуточное или финальное|
|`reason`|—|если отказ|
|`score`|75|значение скоринга|
|`dti`|0.21|значение DTI|
|`external`|`[{service:BKI, status:OK, latency_ms:420}]`|факт вызова|
|`at`|2025-11-01T08:00:00Z|время|
|`actor`|system|кто (system / officer)|

Зачем: без аудита нельзя ни разобрать спорный отказ, ни доказать регулятору, что решение объяснимо.

---

## 3. Пример расчёта 1: app_0001 (APPROVED)

Вход:

|Параметр|Значение|Откуда|
|-|-|-|
|age|32|applications.csv|
|income|120 000|applications.csv|
|employment|employed|applications.csv|
|family|married|applications.csv|
|amount|500 000|applications.csv|
|term|24|applications.csv|
|passport_hash|7d5c9f5f2c8a3a01|applications.csv|
|client_id|cli_0001|applications.csv|

**Шаг 1. Валидация:** все поля заполнены, возраст 32 в 18–70, доход 120000 > 0, сумма 500000 в 10k–5M, срок 24 в 3–60 → OK.

**Шаг 2. СМЭВ:** `hash(cli_0001) mod 10 = 1` → MATCH.

**Шаг 3. БКИ:** `hash(7d5c9f5f2c8a3a01) mod 10 = 2` → score=720, delinquency=false.

**Шаг 4. Скоринг:**

|Признак|Значение|Баллы|
|-|-|-|
|Доход|120 000 (80–150k)|20|
|Занятость|employed|25|
|Возраст|32 (25–50)|20|
|Семья|married|15|
|БКИ|720 (≥700)|10|
|**Итого**||**90**|

**Шаг 5. Лимиты:**

```

monthly_rate = 0.18 / 12 = 0.015

payment = 500000 × 0.015 / (1 − 1.015^(−24))

1.015^24 ≈ 1.4295  →  1.015^(−24) ≈ 0.69955

payment ≈ 7500 / 0.30045 ≈ 24 962 ₽

dti = 24962 / 120000 ≈ 0.208 → 0.21

```

* `dti = 0.21 ≤ 0.5` ✓
* `amount = 500000 ≤ 10 × 120000 = 1 200 000` ✓
* продукт `prod_standard`: amount 500k в 10k–1M ✓, term 24 в 3–60 ✓, age 32 в 18–70 ✓, income 120k ≥ 30k ✓

**Шаг 6. Правила отказа:** ни одно не сработало.

**Шаг 7. Решение:**

```json

{

  "app_id": "app_0001",

  "decision": "APPROVED",

  "reason": "",

  "score": 90,

  "dti": 0.21,

  "amount_approved": 500000,

  "term_approved": 24,

  "rate": 0.18

}

```

Сверка с CSV: `decision=APPROVED`, `dti≈0.21`, `score≈90` → сходится. Небольшое расхождение с CSV (`dti=0.25`) — из-за округлений и другой ставки; в пределах погрешности.

---

## 4. Пример расчёта 2: app_0002 (REFUSED, LOW_INCOME)

Вход:

|Параметр|Значение|
|-|-|
|age|19|
|income|15 000|
|employment|student|
|family|single|
|amount|300 000|
|term|36|
|bki_score|540|
|smev|MATCH|

Шаги 1–3: валидация OK, СМЭВ MATCH, БКИ score=540, delinquency=false.

Шаг 4. Скоринг:

|Признак|Значение|Баллы|
|-|-|-|
|Доход|15 000 (<40k)|0|
|Занятость|student|5|
|Возраст|19 (18–24)|10|
|Семья|single|10|
|БКИ|540 (500–599)|0|
|**Итого**||**25**|

Шаг 5. Лимиты:

```

payment = 300000 × 0.015 / (1 − 1.015^(−36))

1.015^36 ≈ 1.709  →  1.015^(−36) ≈ 0.5851

payment ≈ 4500 / 0.4149 ≈ 10 846 ₽

dti = 10846 / 15000 ≈ 0.72

```

* `dti = 0.72 > 0.5` → должно сработать правило HIGH_DTI (шаг 6, №5).

Шаг 6. Правила отказа:

* №1 age 19 в 18–70 → pass
* №2 employment student ≠ unemployed → pass
* №3 income 15000 не пуст → pass
* **№4 income 15000 < 30000 → REFUSED, LOW_INCOME**

Шаг 7. Решение:

```json

{

  "app_id": "app_0002",

  "decision": "REFUSED",

  "reason": "LOW_INCOME",

  "score": 25,

  "dti": 0.72,

  "amount_approved": null,

  "term_approved": null,

  "rate": null

}

```

Сверка с CSV: `decision=REFUSED`, `reason=LOW_INCOME` → сходится. Обрати внимание: DTI=0.72 тоже выше порога, но правило LOW_INCOME (№4) срабатывает раньше HIGH_DTI (№5) — поэтому в reason именно LOW_INCOME. Это иллюстрирует важность порядка правил.

---

## 5. Пример расчёта 3: app_0005 (расхождение — разбираем)

Вход:

|Параметр|Значение|
|-|-|
|age|28|
|income|60 000|
|employment|self-employed|
|family|single|
|amount|800 000|
|term|48|
|bki_score|610|
|smev|MATCH|

Шаги 1–3: OK, MATCH, БКИ score=610.

Шаг 4. Скоринг:

|Признак|Значение|Баллы|
|-|-|-|
|Доход|60 000 (40–80k)|10|
|Занятость|self-employed|20|
|Возраст|28 (25–50)|20|
|Семья|single|10|
|БКИ|610 (600–699)|5|
|**Итого**||**65**|

Шаг 5. Лимиты:

```

payment = 800000 × 0.015 / (1 − 1.015^(−48))

1.015^48 ≈ 2.0435  →  1.015^(−48) ≈ 0.48936

payment ≈ 12000 / 0.51064 ≈ 23 500 ₽

dti = 23500 / 60000 ≈ 0.39

```

* `dti = 0.39 ≤ 0.5` → HIGH_DTI **не** срабатывает.
* `amount = 800 000 > 10 × 60 000 = 600 000` → срабатывает AMOUNT_LIMIT.

Шаг 6. Правила отказа:

* №1 age 28 → pass
* №2 self-employed → pass
* №3 income не пуст → pass
* №4 income 60000 ≥ 30000 → pass
* №5 dti 0.39 ≤ 0.5 → pass
* №6 bki 610 ≥ 500 → pass
* **№7 amount 800000 > 600000 → REFUSED, AMOUNT_LIMIT**

Шаг 7. Решение (вариант A — по нашей логике):

```json

{

  "app_id": "app_0005",

  "decision": "REFUSED",

  "reason": "AMOUNT_LIMIT",

  "score": 65,

  "dti": 0.39

}

```

Сверка с CSV: в CSV `reason=HIGH_DTI`, но по расчёту DTI=0.39 — **расхождение**.

Как объяснить (вариант A): в `applications.csv` для `app_0005` указан `HIGH_DTI`, но по формуле аннуитета DTI = 0.39, что ниже порога 0.5. Реально срабатывает `AMOUNT_LIMIT`: 800 000 > 10 × 60 000 = 600 000. Возможные причины расхождения в CSV:

* в CSV DTI мог считаться с учётом других кредитов клиента (не только этого);
* в CSV другая annual_rate (например, 0.22 для экспресс-продукта);
* в CSV ошибка разметки.

Наша логика: AMOUNT_LIMIT. Это обоснованно, потому что 800k > 10×60k — объективный факт, не зависящий от ставки.

Альтернатива (вариант B): если команда решит учитывать другие кредиты клиента, DTI может вырасти до >0.5, и тогда сработает HIGH_DTI. Но тогда в `02_scoring_rules.md` нужно явно указать, что DTI = (аннуитет по заявке + платежи по другим кредитам) / доход, и указать источник «других кредитов» (например, поле `existing_debts` в заявке). В текущем CSV такого поля нет — значит, вариант A корректнее.

Что писать в `07`: явно зафиксировать: «В CSV reason=HIGH_DTI, по нашей логике срабатывает AMOUNT_LIMIT, потому что DTI=0.39 < 0.5. Это осознанное расхождение, объяснение — в разделе 5».

---

## 6. Пример расчёта 4: app_0006 (REFUSED, BKI_DELINQUENCY)

Вход:

|Параметр|Значение|
|-|-|
|age|52|
|income|90 000|
|employment|retired|
|family|married|
|amount|400 000|
|term|24|
|bki_score|480|
|delinquency|(предполагаем true, т.к. score < 500)|
|smev|MATCH|

Шаг 4. Скоринг:

|Признак|Баллы|
|-|-|
|Доход 90k (80–150k)|20|
|retired|10|
|age 52 (51–60)|10|
|married|15|
|БКИ 480 (<500)|−20|
|**Итого**|**35**|

Шаг 6. Правила:

* №6 `bki_score 480 < 500` → **REFUSED, BKI_DELINQUENCY**.

Сверка с CSV: `decision=REFUSED`, `reason=BKI_DELINQUENCY` → сходится.

---

## 7. Обработка внешних сбоев

|Ситуация|Что делает конвейер|decision|reason|
|-|-|-|-|
|СМЭВ не ответил за 3 с (2 попытки)|записывает в аудит TIMEOUT, не вызывает БКИ|NEED_MORE_DATA|EXTERNAL_TIMEOUT|
|БКИ не ответил за 3 с (2 попытки)|записывает TIMEOUT, не считает скоринг|NEED_MORE_DATA|EXTERNAL_TIMEOUT|
|Оба не ответили|то же|NEED_MORE_DATA|EXTERNAL_TIMEOUT|
|СМЭВ вернул MISMATCH|не вызывает БКИ, фиксирует|REFUSED|SMEV_MISMATCH|
|БКИ вернул delinquency=true|продолжает скоринг, но правило №6 сработает|REFUSED|BKI_DELINQUENCY|
|БКИ вернул inquiries_90d > 5|не влияет на решение, пишется в аудит|по остальным правилам|—|

Принцип: при любом сомнении — **не одобрять**. Лучше `NEED_MORE_DATA`, чем ошибочный `APPROVED`.

---

## 8. Формулы (сводка)

```

# DTI

monthly_rate = annual_rate / 12

payment = amount × monthly_rate / (1 − (1 + monthly_rate)^(−term))

dti = payment / income



# Максимальная сумма

max_amount = 10 × income



# Score

score = Σ баллов по 5 признакам (см. 02_scoring_rules.md)

score_capped = max(0, score)



# Пороги

APPROVE_THRESHOLD_SCORE = 60

APPROVE_THRESHOLD_DTI = 0.5

MIN_INCOME = 30000

MIN_AGE = 18

MAX_AGE = 70

MIN_TERM = 3

MAX_TERM = 60

MAX_AMOUNT_MULTIPLIER = 10

```

---

## 9. Схема потоков (для draw.io)

Диаграмма строится в draw.io со следующими блоками и стрелками:

* **Вход:** `Application`
* **Ромб:** `Валидация OK?` — нет → `422 / NEED_MORE_DATA`
* **Ромб:** `СМЭВ MATCH?` — нет → `REFUSED (SMEV_MISMATCH)`; timeout → `NEED_MORE_DATA`
* **Ромб:** `БКИ OK?` — timeout → `NEED_MORE_DATA`
* **Прямоугольник:** `Скоринг` → `score`
* **Ромб:** `Лимиты OK?` — нет → `REFUSED (AMOUNT_LIMIT)` или снижение
* **Ромб:** `Правила отказа` — сработало → `REFUSED (reason)`
* **Прямоугольник:** `APPROVED + параметры`
* **Прямоугольник:** `Аудит` (стрелка от каждого ромба)

Цвета:

* зелёный — `APPROVED`;
* красный — `REFUSED`;
* жёлтый — `NEED_MORE_DATA`;
* серый — служебные шаги.

---

## 10. Критерий приёмки

* [x] 8 шагов описаны
* [x] Для каждого шага указано, что происходит при ошибке
* [x] Формула DTI записана и применена на примере app_0001 (dti=0.21)
* [x] Пример app_0001 сходится с applications.csv (APPROVED)
* [x] Пример app_0002 сходится (REFUSED, LOW_INCOME)
* [x] Пример app_0006 сходится (REFUSED, BKI_DELINQUENCY)
* [x] Пример app_0005 разобран с расхождением и объяснением (AMOUNT_LIMIT vs HIGH_DTI)
* [x] Порядок правил отказа (10 правил) совпадает с `02_scoring_rules.md`
* [x] Fallback при таймауте — NEED_MORE_DATA, не APPROVED
* [x] Схема потоков отражает все ветвления
* [x] Формулы выписаны отдельным блоком
* [x] Аудит содержит минимум 6 полей

---

## 11. Что команда должна проверить самостоятельно

1. Пересчитать app_0001, app_0002, app_0005, app_0006 — сходятся ли с CSV.
2. Пересчитать app_0003 (APPROVED, 200k доход, 1M сумма) — проходит ли по лимитам (1M ≤ 10 × 200k = 2M, DTI ≈ 0.36 — должно пройти).
3. Пересчитать app_0004 (NEED_MORE_DATA) — какое поле пустое и какой reason.
4. Пересчитать app_0007 (APPROVED, но SMEV=MISMATCH) — у нас по правилу №8 должно быть REFUSED (SMEV_MISMATCH), а в CSV APPROVED. Это **второе расхождение** — объяснить, почему у вас так, и либо согласиться с CSV, либо обосновать своё решение.
5. Проверить, что при `score < 0` не падает — cap до 0.

---

## 12. Журнал изменений

|Дата|Что изменили|Почему|
|-|-|-|
|Day 2|Создали черновик|—|
|Day 3|Добавили разбор app_0005 и app_0007|Найдены расхождения с CSV|
|Day 4|Уточнили порядок правил|По результатам тестов|
|Day 5|Финализировали|—|
|Day 6|Зафиксирован порог BKI < 500, раздел 13|Единое значение во всех файлах|

## 13. Зафиксированные решения команды

**BKI: порог отказа `bki_score < 500` (или `delinquency = true`).** Единое значение во всех файлах (02, data_dictionary, 08_reasons, app.py, 07). Обоснование: так задано в эталоне (правило №6), 5/5 тестов проходят.

В `applications.csv` 10 заявок с bki 500–549 (app_0013–0016, 0038, 0039, 0060, 0061, 0085, 0086) размечены как BKI_DELINQUENCY — разметка CSV соответствует порогу < 550. Это **осознанное расхождение**: по нашей логике они не отказываются по БКИ (часть из них отклоняется по другим правилам, например LOW_SCORE). Поменять порог на < 550 можно, но тогда нужно менять 02, data_dictionary, 08_reasons и app.py одновременно.

Остальные расхождения с CSV (app_0005, 0007, 0009, 0012, 0019, 0025, 0045, 0064, 0070) перечислены в `CHANGES_architect.md`.
