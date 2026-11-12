#!/usr/bin/env python3
"""京都市バス 車両位置マップ

使い方:
    python bus_viewer.py
    python bus_viewer.py --port 8080

index.html と routes.csv（任意）をこのファイルと同じフォルダに置いてください。
ローカルPCの 127.0.0.1 だけで待ち受け、API中継先は固定しています。
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
UPSTREAM = "https://kyotocity.bus-navigation.jp/wgsys/wgs_kyt/getMapApproachIcon.htm"
ROUTEKEYS_RE = re.compile(r"^\d{1,6}(,\d{1,6}){0,29}$")
PASSNO_RE = re.compile(r"^\d{1,3}$")


def make_ssl_context() -> tuple[ssl.SSLContext, str]:
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "truststore (OS の証明書ストア)"
    except ImportError:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where()), "certifi"
    except ImportError:
        pass
    return ssl.create_default_context(), "Python 既定"


SSL_CTX, SSL_MODE = make_ssl_context()


def call_upstream(route_keys: list[str], pass_no: str) -> bytes:
    n = len(route_keys)
    body = urllib.parse.urlencode({
        "locale": "ja",
        "language": "ja",
        "routeKeys": ",".join(route_keys),
        "fromDisplayPassNo": ",".join([pass_no] * n),
        "fromSignpoleStringKey": ",".join(["0"] * n),
        "destinationCd": "",
        "routeSelectNo": "0",
    }).encode()
    req = urllib.request.Request(
        UPSTREAM, data=body, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (kyoto-bus-map)"
        },
    )
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as response:
        return response.read()


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code: int, obj) -> None:
        self.send_bytes(
            code,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            file_path = BASE_DIR / "index.html"
            if not file_path.is_file():
                self.send_bytes(404, b"index.html not found", "text/plain; charset=utf-8")
                return
            self.send_bytes(200, file_path.read_bytes(), "text/html; charset=utf-8")
            return

        if parsed.path == "/routes.csv":
            file_path = BASE_DIR / "routes.csv"
            if not file_path.is_file():
                self.send_bytes(404, b"routes.csv not found", "text/plain; charset=utf-8")
                return
            self.send_bytes(200, file_path.read_bytes(), "text/csv; charset=utf-8")
            return

        if parsed.path == "/api/vehicles":
            query = urllib.parse.parse_qs(parsed.query)
            keys = re.sub(r"[\s、，]+", ",", query.get("routeKeys", [""])[0]).strip(",")
            pass_no = query.get("fromDisplayPassNo", ["1"])[0] or "1"

            if not ROUTEKEYS_RE.fullmatch(keys):
                self.send_json(400, {"error": "routeKeys は数字をカンマ区切りで指定してください（最大30件）"})
                return
            if not PASSNO_RE.fullmatch(pass_no):
                self.send_json(400, {"error": "fromDisplayPassNo は数字で指定してください"})
                return

            try:
                raw = call_upstream(keys.split(","), pass_no)
                json.loads(raw)
                self.send_bytes(200, raw, "application/json; charset=utf-8")
            except urllib.error.HTTPError as exc:
                self.send_json(502, {"error": f"バス接近情報サイトがエラーを返しました (HTTP {exc.code})"})
            except (urllib.error.URLError, TimeoutError) as exc:
                message = f"バス接近情報サイトに接続できません: {exc}"
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    message += (
                        f" / 証明書の検証に失敗しました（現在の方式: {SSL_MODE}）。"
                        " pip install truststore を実行して再起動してください。"
                    )
                self.send_json(502, {"error": message})
            except (ValueError, json.JSONDecodeError):
                self.send_json(502, {"error": "バス接近情報サイトの応答が JSON ではありません"})
            return

        self.send_bytes(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args) -> None:
        print("  " + fmt % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="京都市バス 車両位置マップ")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"起動しました: {url}（終了は Ctrl+C）")
    print(f"証明書の検証: {SSL_MODE}")
    if SSL_MODE == "Python 既定":
        print("証明書エラーの場合: pip install truststore")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
