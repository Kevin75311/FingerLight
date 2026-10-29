"""``python -m scoreforge`` -- launch the local web GUI.

Options:
    python -m scoreforge                    # http://127.0.0.1:8765
    python -m scoreforge --port 9000
    python -m scoreforge --output-dir out   # where pipeline results land
"""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scoreforge",
                                     description="ScoreForge local web GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", default="output",
                        help="Root directory for pipeline outputs (default: ./output)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open the browser")
    args = parser.parse_args()

    import uvicorn

    from .webui import create_app

    app = create_app(output_root=Path(args.output_dir))
    url = f"http://{args.host}:{args.port}"
    print(f"ScoreForge GUI: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
