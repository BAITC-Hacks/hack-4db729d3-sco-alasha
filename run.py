#!/usr/bin/env python3
"""
Одна команда для всего решения:
  1) пайплайн (если нет out/ или указан --rebuild): raw .parquet -> 3 CSV + graph.pkl;
  2) MCP-сервер graph-intel (Streamable HTTP, :8010) — отдельным процессом;
  3) backend FastAPI + веб-интерфейс (:8000), браузер открывается сам.

  python run.py                 # всё сразу
  python run.py --rebuild       # принудительно пересчитать пайплайн
  python run.py --no-browser --host 0.0.0.0   # для docker / удалённой машины
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
os.chdir(ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-mcp", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    pkl = ROOT / "out" / "graph.pkl"
    sources = list((ROOT / "data").glob("*.parquet")) + [ROOT / "pipeline.py"]
    stale = pkl.exists() and any(s.stat().st_mtime > pkl.stat().st_mtime for s in sources if s.exists())
    if stale:
        print("   данные или pipeline.py новее результатов — пересчитываю")
    if a.rebuild or stale or not pkl.exists():
        print("== 1/3 пайплайн: data/*.parquet -> out/ ==")
        subprocess.run([sys.executable, "pipeline.py", "--data", "data", "--out", "out"], check=True)
    else:
        print("== 1/3 пайплайн: out/ уже есть (пересчитать: python run.py --rebuild) ==")

    mcp_proc = None
    if not a.no_mcp:
        if port_open(8010):
            print("== 2/3 MCP-сервер: порт 8010 уже занят, используем запущенный ==")
        else:
            print("== 2/3 MCP-сервер graph-intel: http://127.0.0.1:8010/mcp ==")
            mcp_proc = subprocess.Popen([sys.executable, "mcp_server.py", "--http"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(60):
                if port_open(8010):
                    break
                time.sleep(0.25)
            print("   MCP-сервер " + ("запущен" if port_open(8010) else "НЕ запустился — агент будет вызывать инструменты напрямую"))

    url = f"http://localhost:{a.port}"
    print(f"== 3/3 backend + веб-интерфейс: {url}   (API: {url}/docs) ==")
    if not a.no_browser:
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    try:
        import uvicorn
        uvicorn.run("server:app", host=a.host, port=a.port, log_level="warning")
    finally:
        if mcp_proc:
            mcp_proc.terminate()


if __name__ == "__main__":
    main()
