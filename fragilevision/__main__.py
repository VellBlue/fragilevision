from __future__ import annotations

import argparse
from pathlib import Path
import threading
import webbrowser

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="FragileVision local evaluation laboratory")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".fragilevision")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser")
    args = parser.parse_args()
    if not args.no_open:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    serve(args.data_dir, port=args.port)


if __name__ == "__main__":
    main()

