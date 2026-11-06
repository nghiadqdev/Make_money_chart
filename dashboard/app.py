"""Flask dashboard for the Kronos paper-trading bot.

Run from the project root:

    python dashboard/app.py
    # hoặc: python dashboard/app.py --port 7071 --config config/settings.yaml

Open http://127.0.0.1:7071
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from flask import Flask, jsonify, render_template, request  # noqa: E402

from dashboard.service import DashboardService  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False
SERVICE: DashboardService | None = None


def service() -> DashboardService:
    if SERVICE is None:
        raise RuntimeError("DashboardService chưa được khởi tạo")
    return SERVICE


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/status")
def status():
    try:
        return jsonify(service().status())
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("status failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/start")
def start():
    return jsonify(service().start())


@app.post("/api/stop")
def stop():
    return jsonify(service().stop())


@app.post("/api/force-close")
def force_close():
    payload = request.get_json(silent=True) or {}
    timeout = float(payload.get("timeout_sec", 30))
    return jsonify(service().force_close(timeout_sec=timeout))


@app.post("/api/reset")
def reset():
    return jsonify(service().reset())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kronos paper-trading dashboard")
    parser.add_argument("--config", default=str(REPO_ROOT / "config/settings.yaml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7071)
    return parser


def main() -> int:
    global SERVICE
    args = build_parser().parse_args()
    SERVICE = DashboardService(args.config)
    print("=" * 64)
    print("Kronos paper-trading dashboard")
    print(f"Config : {SERVICE.config_path}")
    print(f"Symbol : {SERVICE.settings.market.symbol} {SERVICE.settings.market.timeframe}")
    print(f"URL    : http://{args.host}:{args.port}")
    print("Paper only | 100 USDT | không gửi lệnh thật")
    print("=" * 64)
    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
