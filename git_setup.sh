#!/usr/bin/env bash
# Запуск из корня репозитория команды. Замените N на номер команды.
set -e
git checkout -b team_N_case_2 2>/dev/null || git checkout team_N_case_2
mkdir -p final && cp -r ./final_unzipped/* final/ 2>/dev/null || true
git add final
git commit -m "Case 2: final artifacts (requirements, API schemas, ИБ, app.py, decisions)"
git push -u origin team_N_case_2
