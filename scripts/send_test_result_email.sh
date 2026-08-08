#!/usr/bin/env bash
# Email the week-test result and the dashboard URL.
#
#   ./scripts/send_test_result_email.sh              # send
#   ./scripts/send_test_result_email.sh --dry-run    # print, send nothing
#
# Requires SMTP_USERNAME / SMTP_PASSWORD / EMAIL_FROM in .env. Without them this
# prints what it would have sent and exits non-zero, rather than failing silently.

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

dry_run=false
[[ "${1:-}" == "--dry-run" ]] && dry_run=true

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

artifact="artifacts/week_test/latest.json"
if [[ ! -f "$artifact" ]]; then
  echo "検証結果がありません。先に次を実行してください:" >&2
  echo "  .venv/bin/python -m cli week-test --from-date 2026-08-01 --to-date 2026-08-07" >&2
  exit 1
fi

python_bin=".venv/bin/python"
[[ -x "$python_bin" ]] || python_bin="python3"

recipient="${EMAIL_TO:-}"
if [[ -z "$recipient" ]]; then
  echo "EMAIL_TO が .env に設定されていません。" >&2
  exit 1
fi

DRY_RUN="$dry_run" ARTIFACT="$artifact" RECIPIENT="$recipient" "$python_bin" - <<'PY'
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

report = json.loads(Path(os.environ["ARTIFACT"]).read_text(encoding="utf-8"))
window = report["generated_for"]
totals = report["totals"]
rule = report["rule"]
app_url = os.environ.get("APP_URL", "").strip() or "http://192.168.3.11:8501"


def percent(value: object) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def yen(value: object) -> str:
    return "—" if value is None else f"{float(value):,.0f}円"


lines = [
    f"検証期間: {window['from']} 〜 {window['to']}",
    f"学習: 各予測日の直前 {window['training_window_sessions']} 営業日",
    (
        "BUY条件: 予測リターン > "
        f"{rule['return_threshold'] * 100:.2f}% かつ 上昇確率 >= "
        f"{rule['probability_threshold'] * 100:.0f}%"
    ),
    "",
    "■ 結果",
    f"  予測件数    {totals['predictions']}",
    f"  BUYシグナル {totals['buy_signals']}",
    f"  勝ち / 負け {totals['wins']} / {totals['losses']}",
    f"  勝率        {percent(totals['win_rate'])}",
    f"  勝ち金額    {yen(totals['gross_win_jpy'])}",
    f"  負け金額    {yen(totals['gross_loss_jpy'])}",
    (
        "  金額ベース勝率 "
        + (
            "負けなし"
            if totals["money_win_ratio"] is None
            else f"{totals['money_win_ratio']:.3f}"
        )
    ),
    f"  純損益      {yen(totals['net_profit_jpy'])}",
    f"  方向的中率  {percent(totals['direction_accuracy'])}",
    "",
    "■ 日別",
]
for row in report["daily"]:
    lines.append(
        f"  {row['date']}  予測{row['predictions']:>3} BUY{row['buy_signals']:>3}"
        f"  勝率 {percent(row['win_rate'])}  純損益 {yen(row['net_profit_jpy'])}"
    )

bought = [row for row in report["predictions"] if row["signal"] == "BUY"]
lines += ["", "■ 実際に買った銘柄"]
if not bought:
    lines.append("  条件を満たした銘柄はありませんでした。")
for row in sorted(bought, key=lambda item: (item["date"], item["ticker"])):
    predicted_difference = row.get("predicted_price_difference")
    actual_difference = row.get("actual_price_difference")
    lines.append(
        f"  {row['date']} {row['ticker']}"
        f"  予測{row['predicted_return'] * 100:+.2f}%"
        + (
            f"({predicted_difference:+,.0f}円)"
            if predicted_difference is not None
            else ""
        )
        + f"  実績{row['actual_return'] * 100:+.2f}%"
        + (f"({actual_difference:+,.0f}円)" if actual_difference is not None else "")
        + f"  確率{row['probability_up'] * 100:.1f}%"
        f"  {row['shares']}株  {row['net_profit_jpy']:+,.0f}円"
    )

lan_url = os.environ.get("LAN_URL", "http://192.168.3.11:8501").strip()
repo_url = os.environ.get("REPO_URL", "").strip()
lines += [
    "",
    "■ リンク",
    f"  ダッシュボード（どこからでも）: {app_url}",
    "     ↑ 普段はこれ。スマホの共有ボタンからホーム画面に追加できます。",
    "     「テスト」ページに日別の内訳、寄り付き/大引けの予測と実績、",
    "     各指標の係数の推移が入っています。",
    f"  自宅Wi-Fi内のスマホから: {lan_url}",
    "  このMacから: http://localhost:8501",
]
if repo_url:
    lines.append(f"  ソースコード: {repo_url}")
lines += [
    "",
    "■ 注意",
]
lines += [f"  - {caveat}" for caveat in report["caveats"]]
lines += [
    "  - BUYシグナルが少ないため、この勝率は有効性の証拠になりません。",
    "  - 研究用の情報であり、投資助言ではありません。利益を保証しません。",
]
body = "\n".join(lines)

subject = (
    f"[日本株予測] 検証結果 {window['from']}〜{window['to']} "
    f"勝率 {percent(totals['win_rate'])} / BUY {totals['buy_signals']}件"
)

if os.environ["DRY_RUN"] == "true":
    print(f"To: {os.environ['RECIPIENT']}")
    print(f"Subject: {subject}\n")
    print(body)
    sys.exit(0)

username = os.environ.get("SMTP_USERNAME", "").strip()
password = os.environ.get("SMTP_PASSWORD", "").strip()
sender = os.environ.get("EMAIL_FROM", "").strip() or username
if not username or not password:
    print("送信できません: SMTP_USERNAME / SMTP_PASSWORD が未設定です。", file=sys.stderr)
    print("START_HERE.md の手順1でGmailアプリパスワードを設定してください。\n", file=sys.stderr)
    print("--- 送信予定だった内容 ---", file=sys.stderr)
    print(f"To: {os.environ['RECIPIENT']}", file=sys.stderr)
    print(f"Subject: {subject}\n", file=sys.stderr)
    print(body, file=sys.stderr)
    sys.exit(2)

message = EmailMessage()
message["Subject"] = subject
message["From"] = sender
recipients = [
    address.strip()
    for address in os.environ["RECIPIENT"].split(",")
    if address.strip()
]
message["To"] = ", ".join(recipients)
message.set_content(body)

host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
port = int(os.environ.get("SMTP_PORT", "587"))

# The python.org macOS build ships without root certificates, so the default
# context cannot verify Gmail's chain. certifi's bundle is already a dependency
# here; use it rather than disabling verification, which would expose the app
# password to anyone able to intercept the connection.
try:
    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    context = ssl.create_default_context()

with smtplib.SMTP(host, port, timeout=30) as server:
    server.starttls(context=context)
    server.login(username, password)
    server.send_message(message, to_addrs=recipients)
print(f"送信しました: {', '.join(recipients)}")
PY
