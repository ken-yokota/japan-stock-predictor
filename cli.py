"""Single entry point for every operational command.

GitHub Actions and a human operator must be able to invoke the same code path,
so the scheduling layer holds no business logic of its own. Each subcommand here
delegates to the module that already owns the work and returns its exit code
unchanged; nothing is reimplemented, and no timing decision lives in this file.

    python -m cli phase0
    python -m cli bootstrap-history --from-date 2023-08-01 --to-date 2026-08-07
    python -m cli walk-forward
    python -m cli morning --prediction-date 2026-08-10
    python -m cli send-email --prediction-date 2026-08-10 --dry-run
    python -m cli update-open --prediction-date 2026-08-10
    python -m cli close --prediction-date 2026-08-10
    python -m cli config-check

Unknown flags are forwarded verbatim to the delegate, so each subcommand's own
``--help`` remains authoritative:

    python -m cli morning -- --help
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

_Delegate = Callable[[Sequence[str]], int]

_HELP_TEXT = """使い方: python -m cli <command> [options]

コマンド:
  phase0             無料データの取得可否を検証する (scripts.phase0_data_feasibility)
  bootstrap-history  初期履歴をまとめて取得する (scripts.bootstrap_history)
  walk-forward       estimated-PIT walk-forward OOSを一括生成する
  morning            08:20の朝pipelineを実行する (scripts.run_morning_prediction)
  send-email         保存済みsetの朝メールを送る (scripts.send_morning_email)
  update-open        寄り付き後のActual Openを観測する (scripts.update_open)
  close              大引け後の実績・損益を更新する (scripts.run_close_update)
  buy-all            全銘柄を毎日買った場合の対照結果を出す: モデル判定なし
  week-test          直近期間をDBなしでwalk-forward検証する: 研究用
  compare-features   予測要素の組を同一条件で比較し、採否を符号検定で判定する
  config-check       秘密情報なしでYAML/symbol構成を検証する (data.fetch)
  dashboard          Streamlit dashboardをローカル起動する

各コマンドの詳細な引数は、そのコマンドに --help を付けて確認してください。
"""


def _run_script(module_name: str, program: str, argv: Sequence[str]) -> int:
    """Invoke a script's ``main`` with ``sys.argv`` set to its own arguments.

    The operational scripts each own an ``argparse`` parser that reads
    ``sys.argv`` directly, so the process argv is swapped for the delegate's and
    restored afterwards. ``program`` becomes the parser's usage prefix, keeping
    ``--help`` output honest about how the command was invoked.
    """

    from importlib import import_module

    delegate = import_module(module_name).main
    original = sys.argv
    sys.argv = [program, *argv]
    try:
        return int(delegate() or 0)
    finally:
        sys.argv = original


def _phase0(argv: Sequence[str]) -> int:
    return _run_script("scripts.phase0_data_feasibility", "python -m cli phase0", argv)


def _bootstrap_history(argv: Sequence[str]) -> int:
    return _run_script(
        "scripts.bootstrap_history", "python -m cli bootstrap-history", argv
    )


def _walk_forward(argv: Sequence[str]) -> int:
    return _run_script("scripts.run_walk_forward", "python -m cli walk-forward", argv)


def _morning(argv: Sequence[str]) -> int:
    return _run_script("scripts.run_morning_prediction", "python -m cli morning", argv)


def _send_email(argv: Sequence[str]) -> int:
    return _run_script("scripts.send_morning_email", "python -m cli send-email", argv)


def _update_open(argv: Sequence[str]) -> int:
    return _run_script("scripts.update_open", "python -m cli update-open", argv)


def _close(argv: Sequence[str]) -> int:
    return _run_script("scripts.run_close_update", "python -m cli close", argv)


def _week_test(argv: Sequence[str]) -> int:
    return _run_script("scripts.run_week_test", "python -m cli week-test", argv)


def _compare_features(argv: Sequence[str]) -> int:
    return _run_script(
        "scripts.run_feature_comparison", "python -m cli compare-features", argv
    )


def _buy_all(argv: Sequence[str]) -> int:
    return _run_script("scripts.run_buy_all_reference", "python -m cli buy-all", argv)


def _config_check(argv: Sequence[str]) -> int:
    from data.fetch import main

    return int(main(["config-check", *argv]) or 0)


def _dashboard(argv: Sequence[str]) -> int:
    """Start Streamlit in-process so the dashboard needs no separate command."""

    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError:
        print(
            "streamlitが未インストールです。"
            "python -m pip install -r requirements.txt を実行してください。",
            file=sys.stderr,
        )
        return 1
    sys.argv = ["streamlit", "run", "app.py", *argv]
    return int(streamlit_cli.main() or 0)


_COMMANDS: dict[str, _Delegate] = {
    "phase0": _phase0,
    "bootstrap-history": _bootstrap_history,
    "walk-forward": _walk_forward,
    "morning": _morning,
    "send-email": _send_email,
    "update-open": _update_open,
    "close": _close,
    "buy-all": _buy_all,
    "week-test": _week_test,
    "compare-features": _compare_features,
    "config-check": _config_check,
    "dashboard": _dashboard,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one subcommand and return its exit code."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help", "help"}:
        print(_HELP_TEXT)
        return 0
    command, *rest = arguments
    delegate = _COMMANDS.get(command)
    if delegate is None:
        print(f"未知のコマンド: {command}\n", file=sys.stderr)
        print(_HELP_TEXT, file=sys.stderr)
        return 2
    # Allow "python -m cli morning -- --help" to reach the delegate untouched.
    if rest and rest[0] == "--":
        rest = rest[1:]
    return delegate(rest)


if __name__ == "__main__":
    raise SystemExit(main())
