import csv
import sys

CHECKS = [
    ("T2-1", "app_0001", "APPROVED",       "",                "score_dti"),
    ("T2-2", "app_0006", "REFUSED",        "BKI_DELINQUENCY", ""),
    ("T2-3", "app_0004", "NEED_MORE_DATA", "MISSING_INCOME",  ""),
    ("T2-4", "app_0002", "REFUSED",        "LOW_INCOME",      ""),
    ("T2-5", "app_0005", "REFUSED",        "AMOUNT_LIMIT",    ""),
]

def main(path="decisions.csv"):
    rows = {r["app_id"]: r for r in csv.DictReader(open(path, encoding="utf-8"))}
    passed = 0
    print(f"{'тест':5} {'app':10} {'статус':6} {'decision':15} {'reason':18} {'score':5} {'dti':5}  детали")
    print("-" * 100)

    for tid, aid, exp_dec, exp_rsn, extra in CHECKS:
        r = rows.get(aid)
        if not r:
            print(f"{tid:5} {aid:10} НЕТ    — заявка не найдена в {path}")
            continue

        ok = r["decision"] == exp_dec
        if exp_rsn:
            ok = ok and r["reason"] == exp_rsn

        details = ""
        if extra == "score_dti" and r["score"] and r["dti"]:
            s, d = float(r["score"]), float(r["dti"])
            s_ok, d_ok = s >= 60, d <= 0.5
            ok = ok and s_ok and d_ok
            details = f"score>=60? {s_ok}, dti<=0.5? {d_ok}"

        passed += ok
        mark = "OK" if ok else "FAIL"
        print(f"{tid:5} {aid:10} {mark:6} {r['decision']:15} {r['reason']:18} {r['score']:5} {r['dti']:5}  {details}")

    print(f"\nПройдено: {passed}/{len(CHECKS)}")
    return 0 if passed == len(CHECKS) else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "decisions.csv"))