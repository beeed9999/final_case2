#!/usr/bin/env python3
"""
CLI-прототип кредитного конвейера — кейс 2.

Использование:
    python app.py applications.csv
    python app.py applications.csv --out decisions.csv
    python app.py applications.csv --verbose   # score по компонентам

Читает CSV, прогоняет каждую заявку через пайплайн, пишет решения.

TODO для команды:
    1. Дописать validate() — правила из 03_data_dictionary.md.
    2. Дописать bki_emulator() — 3-4 сценария.
    3. Дописать smev_emulator() — MATCH / MISMATCH.
    4. Дописать scoring() — веса из 02_scoring_rules.md.
    5. Дописать decide() — порядок правил отказа.
    6. Считать DTI по формуле из 02.
    7. Записать decisions.csv с колонками: app_id, decision, reason, score, dti.
"""
import csv
import hashlib
import sys

VERBOSE = "--verbose" in sys.argv
FIELDNAMES_OUT = ["app_id", "decision", "reason", "score", "dti", "amount_approved", "term_approved"]


def bki_emulator(passport_hash):
    """
    Детерминированный эмулятор БКИ.
    h = md5(passport_hash) mod 10:
      0..5 → score = 600 + h*20 (600–700), delinquency=False
      6..8 → score = 450 + (h-6)*50 (450–550), delinquency=False
      9    → score = 400, delinquency=True
    """
    h = int(hashlib.md5(passport_hash.encode()).hexdigest(), 16) % 10
    if h <= 5:
        return (600 + h * 20, False)
    if h <= 8:
        return (450 + (h - 6) * 50, False)
    return (400, True)

def smev_emulator(client_id):
    """
    h = md5(client_id) mod 10:
      h == 0 → MISMATCH
      иначе  → MATCH
    (TIMEOUT в этой версии не эмулируется — описан в 06, но для тестов
     достаточно MATCH/MISMATCH; если нужен TIMEOUT — добавь флаг.)
    """
    h = int(hashlib.md5(client_id.encode()).hexdigest(), 16) % 10
    return "MISMATCH" if h == 0 else "MATCH"


EMPLOYMENT_ENUM = {"employed", "self-employed", "student", "retired", "unemployed"}
FAMILY_ENUM = {"single", "married", "children"}


def validate(app):
    """Возвращает список [(field, code), ...].
    Пустые обязательные поля → MISSING_<FIELD>.
    Неверные значения → <FIELD>_INVALID / _OUT_OF_RANGE."""
    errors = []

    # age: 18..70
    if not app.get("age"):
        errors.append(("age", "MISSING_AGE"))
    else:
        try:
            age = int(app["age"])
            if age < 18 or age > 70:
                errors.append(("age", "AGE_OUT_OF_RANGE"))
        except ValueError:
            errors.append(("age", "AGE_INVALID"))

    # income: > 0
    if not app.get("income"):
        errors.append(("income", "MISSING_INCOME"))
    else:
        try:
            income = float(app["income"])
            if income <= 0:
                errors.append(("income", "INCOME_INVALID"))
        except ValueError:
            errors.append(("income", "INCOME_INVALID"))

    # employment: enum из 5
    emp = app.get("employment")
    if not emp:
        errors.append(("employment", "MISSING_EMPLOYMENT"))
    elif emp not in EMPLOYMENT_ENUM:
        errors.append(("employment", "INVALID_EMPLOYMENT"))

    # family: enum из 3
    fam = app.get("family")
    if not fam:
        errors.append(("family", "MISSING_FAMILY"))
    elif fam not in FAMILY_ENUM:
        errors.append(("family", "FAMILY_INVALID"))

    # amount: 10_000..5_000_000
    if not app.get("amount"):
        errors.append(("amount", "MISSING_AMOUNT"))
    else:
        try:
            amount = float(app["amount"])
            # формат API допускает до 5M, но продукт prod_standard — до 1M (Ограничение 2)
            if amount < 10_000 or amount > 1_000_000:
                errors.append(("amount", "AMOUNT_OUT_OF_RANGE"))
        except ValueError:
            errors.append(("amount", "AMOUNT_INVALID"))

    # term: 3..60
    if not app.get("term"):
        errors.append(("term", "MISSING_TERM"))
    else:
        try:
            term = int(app["term"])
            if term < 3 or term > 60:
                errors.append(("term", "TERM_OUT_OF_RANGE"))
        except ValueError:
            errors.append(("term", "TERM_INVALID"))

    # bki_score: 300..850 (если передан)
    bs = app.get("bki_score")
    if bs:
        try:
            if not 300 <= int(bs) <= 850:
                errors.append(("bki_score", "BKI_SCORE_INVALID"))
        except ValueError:
            errors.append(("bki_score", "BKI_SCORE_INVALID"))

    # passport_hash: 16 hex (если передан)
    ph = app.get("passport_hash")
    if ph:
        if not (len(ph) == 16 and all(c in "0123456789abcdef" for c in ph)):
            errors.append(("passport_hash", "PASSPORT_HASH_INVALID"))

    return errors

def score(app, bki_score, delinquency):
    """
    Возвращает (total, components).
    Веса:
      income:     >=150k→30, 80–150k→20, 40–80k→10, <40k→0
      employment: employed→25, self-employed→20, retired→10, student→5, unemployed→0
      age:        25–50→20, 18–24 или 51–60→10, 61–70→5
      family:     married→15, children→15, single→10
      BKI:        >=700→10, 600–699→5, 500–599→0, <500 или delinquency→−20
    total cap: max(0, total)
    """
    income = float(app["income"])
    s_income = 30 if income >= 150_000 else 20 if income >= 80_000 else 10 if income >= 40_000 else 0

    s_emp = {"employed": 25, "self-employed": 20, "retired": 10,
             "student": 5, "unemployed": 0}[app["employment"]]

    age = int(app["age"])
    if 25 <= age <= 50:
        s_age = 20
    elif 18 <= age <= 24 or 51 <= age <= 60:
        s_age = 10
    elif 61 <= age <= 70:
        s_age = 5
    else:
        s_age = 0

    s_family = {"married": 15, "children": 15, "single": 10}[app["family"]]

    if delinquency or bki_score < 500:
        s_bki = -20
    elif bki_score >= 700:
        s_bki = 10
    elif bki_score >= 600:
        s_bki = 5
    else:  # 500..599
        s_bki = 0

    total = s_income + s_emp + s_age + s_family + s_bki
    components = {
        "income": s_income, "employment": s_emp, "age": s_age,
        "family": s_family, "bki": s_bki,
    }
    return max(0, total), components


def compute_dti(amount, term, income):
    mr = 0.18 / 12
    payment = amount * mr / (1 - (1 + mr) ** (-term))
    return round(payment / income, 2) if income else None


def decide(app, score_total, dti, delinquency, smev_status, bki_score):
    """
    Порядок правил (первое сработавшее — финальное):
      1. age < 18 или > 70          → REFUSED, AGE_OUT_OF_RANGE
      2. employment = unemployed    → REFUSED, INVALID_EMPLOYMENT
      3. income пуст                → NEED_MORE_DATA, MISSING_INCOME
      4. income < 30_000            → REFUSED, LOW_INCOME
      5. dti > 0.5                  → REFUSED, HIGH_DTI
      6. bki < 500 или delinquency  → REFUSED, BKI_DELINQUENCY
      7. amount > 10 × income       → REFUSED, AMOUNT_LIMIT
      8. smev == MISMATCH           → REFUSED, SMEV_MISMATCH
      9. score < 60                 → REFUSED, LOW_SCORE
      10. всё прошло                → APPROVED
    """
    age = int(app["age"]) if app.get("age") else None
    income = float(app["income"]) if app.get("income") else None
    amount = float(app["amount"])

    # 1
    if age is None or age < 18 or age > 70:
        return "REFUSED", "AGE_OUT_OF_RANGE"
    # 2
    if app["employment"] == "unemployed":
        return "REFUSED", "INVALID_EMPLOYMENT"
    # 3
    if income is None:
        return "NEED_MORE_DATA", "MISSING_INCOME"
    # 4
    if income < 30_000:
        return "REFUSED", "LOW_INCOME"
    # 5
    if dti is not None and dti > 0.5:
        return "REFUSED", "HIGH_DTI"
    # 6
    if bki_score < 500 or delinquency:
        return "REFUSED", "BKI_DELINQUENCY"
    # 7
    if amount > 10 * income:
        return "REFUSED", "AMOUNT_LIMIT"
    # 8 (СМЭВ — по эталону 07: правило №8)
    if smev_status == "MISMATCH":
        return "REFUSED", "SMEV_MISMATCH"
    # 9
    if score_total < 60:
        return "REFUSED", "LOW_SCORE"
    # 10
    return "APPROVED", ""


def process(app):
    errors = validate(app)
    if errors:
        # Если поле ПУСТОЕ — ждем данные (NEED_MORE_DATA)
        # Если данные НЕВЕРНЫЕ — отказываем (REFUSED)
        missing_errors = [e for e in errors if e[1].startswith("MISSING_")]

        # Правила №1 и №2 стоят выше №3: отказ по возрасту/занятости важнее запроса данных
        if missing_errors:
            hard = None
            try:
                if app.get("age") and not (18 <= int(app["age"]) <= 70):
                    hard = "AGE_OUT_OF_RANGE"
            except ValueError:
                pass
            if hard is None and app.get("employment") == "unemployed":
                hard = "INVALID_EMPLOYMENT"
            if hard:
                return {"app_id": app["app_id"], "decision": "REFUSED",
                        "reason": hard, "score": "", "dti": "",
                        "amount_approved": "", "term_approved": ""}

        if missing_errors:
            # Приоритет: MISSING_INCOME важнее MISSING_AGE
            priority = {"MISSING_INCOME": 0, "MISSING_AGE": 1, "MISSING_EMPLOYMENT": 2,
                        "MISSING_FAMILY": 3, "MISSING_AMOUNT": 4, "MISSING_TERM": 5}
            missing_errors.sort(key=lambda e: priority.get(e[1], 99))
            return {"app_id": app["app_id"], "decision": "NEED_MORE_DATA",
                    "reason": missing_errors[0][1], "score": "", "dti": "",
                    "amount_approved": "", "term_approved": ""}
        else:
            # Ошибка в данных (возраст, занятость, сумма) — отказ
            return {"app_id": app["app_id"], "decision": "REFUSED",
                    "reason": errors[0][1], "score": "", "dti": "",
                    "amount_approved": "", "term_approved": ""}
    if app.get("bki_score"):
        bki_score = int(app["bki_score"])
        delinquency = (bki_score < 500)
    else:
        bki_score, delinquency = bki_emulator(app["passport_hash"])
    # берём статус СМЭВ из заявки, если он есть; иначе эмулятор
    smev = app.get("smev_status") or smev_emulator(app["client_id"])
    # СМЭВ не ответил — не одобряем автоматически (эталон 07, ИБ U-6)
    if smev == "TIMEOUT":
        return {"app_id": app["app_id"], "decision": "NEED_MORE_DATA",
                "reason": "EXTERNAL_TIMEOUT", "score": "", "dti": "",
                "amount_approved": "", "term_approved": ""}
    score_total, components = score(app, bki_score, delinquency)
    if VERBOSE:
        print(f"    {app['app_id']}: score={score_total} {components} bki={bki_score} smev={smev}")
    dti = compute_dti(float(app["amount"]), int(app["term"]), float(app["income"]))
    decision, reason = decide(app, score_total, dti, delinquency, smev, bki_score)

    approved_amount = app["amount"] if decision == "APPROVED" else ""
    approved_term = app["term"] if decision == "APPROVED" else ""
    return {"app_id": app["app_id"], "decision": decision, "reason": reason,
            "score": score_total, "dti": dti,
            "amount_approved": approved_amount, "term_approved": approved_term}

def main():
    if len(sys.argv) < 2:
        print("Использование: python app.py applications.csv [--out decisions.csv]")
        sys.exit(1)

    src = sys.argv[1]
    out = "decisions.csv"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    with open(src, encoding="utf-8") as f:
        apps = list(csv.DictReader(f))

    results = [process(a) for a in apps]

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES_OUT)
        w.writeheader()
        for r in results:
            w.writerow(r)

    stats = {}
    for r in results:
        stats[r["decision"]] = stats.get(r["decision"], 0) + 1
    print(f"OK: {out}")
    print(f"    обработано: {len(results)}")
    print(f"    решения:    {stats}")


if __name__ == "__main__":
    main()