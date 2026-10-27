"""Start the local server and open the dashboard: `uv run python -m fifteen_bot`."""

import argparse
import logging
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="15-minute market trading bot for Public.com")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open the dashboard automatically")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"15-Minute Bot running at {url}  (Ctrl+C to stop)")
    # Bound to localhost only: the server holds your API key and can place orders.
    uvicorn.run("fifteen_bot.app:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
