# 15-Minute Bot

A dashboard that runs on your own computer, in your browser, and trades Public.com's 15-minute
prediction markets (BTC, ETH, Gold, EUR/USD, …) using rules you set for each market.

> **The bot places real orders with real money.** Nothing is traded until you press **Start**,
> which asks you to confirm first. You can lose the full amount you pay for each contract.

## Getting started

### 1. What you need

- **A computer that stays on while the bot trades.** The bot only trades while the app is running
  and the computer is awake. It's built and tested on macOS.
- **A Public.com brokerage account** that can trade prediction markets. If you can buy a 15-minute
  market (e.g. "BTC 15 min") in Public's own app, you're set.
- **uv**, the Python tool that installs and runs the app. You don't need to install Python yourself;
  uv downloads the right version the first time.

  ```sh
  brew install uv                                    # with Homebrew
  # or
  curl -LsSf https://astral.sh/uv/install.sh | sh    # without Homebrew
  ```

  Check it worked with `uv --version`.

### 2. Get the code and install

Put the project folder on your computer, open a terminal in it, and install everything:

```sh
cd 15-minute-bot
uv sync
```

The first run takes a minute, because uv downloads Python 3.14 and the app's libraries into a
private `.venv` folder in the project. Nothing is installed system-wide.

### 3. Create a Public API key

1. Log in to public.com and open the API settings page: <https://public.com/settings/security/api>.
   The setup wizard also links to it.
2. Generate a secret key and copy it.

Treat the key like a password: anyone who has it can trade in your account. If it's ever exposed,
delete it on that same page and it stops working immediately.

### 4. Start the app

```sh
uv run python -m fifteen_bot
```

Your browser opens at <http://127.0.0.1:8765>. Leave the terminal window open, because closing it
stops the app.

To keep your Mac awake while the bot runs (it stops trading if the Mac sleeps), start it like this
instead:

```sh
caffeinate -i uv run python -m fifteen_bot
```

`caffeinate -i` stops the Mac idle-sleeping, but closing a laptop lid still puts it to sleep.

### 5. Follow the setup wizard

The first time, a wizard walks you through setup:

1. **Connect**: paste your API key. Leave **Remember on this computer** ticked to save it in your
   system keychain (macOS Keychain), so next time you connect with one click. The key is never
   written to the project folder.
2. **Account**: pick the brokerage account the bot should trade in.
3. **Markets**: choose which 15-minute markets to trade. Start with one or two.
4. **Your rule**: set when and what to buy. A live example shows what a trade would cost and pay.
5. **Review**: check what each trade can cost, then **Start trading**, or **Finish without
   starting** and press Start on the dashboard later.

### 6. Day to day

- **Start/stop trading:** the **Engine** button in the top bar. Stopping it only stops new trades.
  Contracts you already bought are never sold. They settle as normal, and their results still
  show up while the app is connected.
- **Change a market's rule:** **Edit** on its card, or **Edit all strategies** for every market.
- **Turn a market on/off:** the switch on its card.
- **See results:** the dashboard shows total P&L, win rate, a per-market table, every trade and an
  activity log. A trade shows as won/lost a few minutes after its window closes, once Public
  settles it.
- **Next time:** run `uv run python -m fifteen_bot` again and click **Connect with saved key**. Your
  markets, rules and history are kept, but the engine always starts **stopped**, so press Start
  when you're ready.
- **Stop the app:** press **Ctrl+C** in its terminal.

## Updating the app

After the code changes, stop the app (Ctrl+C), then:

```sh
uv sync                        # only needed if dependencies changed; safe to run anyway
uv run python -m fifteen_bot
```

Always restart after an update. The browser loads the new page right away, but the running server
keeps the old code until it restarts, and the mix causes errors like "Method Not Allowed".

## Troubleshooting

| Problem | Fix |
|---|---|
| "Method Not Allowed" or other odd errors after an update | The server is still running old code. Restart it (Ctrl+C, then start again). |
| "Public didn't accept that key" | Copy the whole key again, or generate a new one. Check it wasn't deleted in Public's API settings. |
| "Couldn't reach Public" | Check your internet connection and try again. |
| Port 8765 is already in use | Another copy is probably still running. Stop it, or start this one with `--port 9000`. |
| The browser doesn't open | Go to <http://127.0.0.1:8765> yourself, or start with `--no-browser` and open it when you like. |
| A market card says "Multi-outcome market — not supported yet" | Markets with more than two outcomes (e.g. "Crypto Leader") can't be traded yet. Remove it. |
| A card says "Waiting for fresh prices" | Normal for a moment after startup or a network blip. If it persists, check your connection. |
| Use a different API key | Disconnect, then on the connect screen choose **Use a different key**. |
| Forget the saved key | Open **Keychain Access**, search for `15-minute-bot` and delete it. Or run: `uv run python -c "import keyring; keyring.delete_password('15-minute-bot', 'public-api-secret-key')"` |
| Start completely fresh | Stop the app and delete `data/bot.db`. This erases your markets, rules and trade history; the setup wizard runs again. |

## Where things are stored

| What | Where |
|---|---|
| Markets, rules, trade history, chosen account | `data/bot.db` in the project folder (SQLite). Set `FIFTEEN_BOT_DATA=/some/dir` to keep it elsewhere. |
| API key (if "Remember" was ticked) | Your system keychain, under `15-minute-bot` |
| App logs | The terminal window the app runs in |

The app only listens on your own computer (`127.0.0.1`), so other devices on your network can't
reach it, and it refuses requests from other websites.

## How trading works

- A "market" is a recurring series such as `KXBTC15M`. Every 15 minutes it opens a new window
  (event). Each market has its own rule and can be switched on/off independently.
- Rule: between *start* and *stop* seconds before the window closes, buy YES/NO/either side
  once its ask is within *[lowest, highest]* chance (price in ¢ = implied %). At most **one trade
  per market per window**. Defaults: 2:00 → 0:10 left, 80–90%, either side, 1 contract.
- Orders are DAY limit buys at the ask rounded up to the cent, so you never pay more than the
  price the rule matched. Unfilled orders are cancelled when the window closes.
- After a window settles the trade is marked won/lost and P&L is recorded
  (`$1 × contracts` if won, minus cost and Public's estimated fees).

### Staying under Public's rate limit

Public allows 10 API requests per second. Every request the app makes goes through a limiter
(`fifteen_bot/ratelimit.py`) that sends at most 8 per second, evenly spaced, with orders and cancels
ahead of routine price checks. If Public still answers 429, all requests pause for its Retry-After
time. To keep traffic low, prices for all markets come from one batched quote request and open
orders from one order search. Typical use is 1–3 requests per second. Other tools calling Public's
API for the same account at the same time probably count toward the same limit.

## For developers

```sh
uv run pytest                                          # run the tests
uv run python -m fifteen_bot --port 9000 --no-browser  # other port, no auto-open
```

```
fifteen_bot/
  strategy.py   strategy model and pure decision logic (tested)
  engine.py     trading loop: window discovery, quotes, orders, settlement
  ratelimit.py  keeps requests under Public's 10/s limit (tested)
  store.py      SQLite storage (data/bot.db): markets, settings, trades
  app.py        FastAPI JSON API + static dashboard, localhost-only
  static/       dashboard (plain HTML/CSS/JS, no build step)
tests/          uv run pytest
```

Uses the official [`publicdotcom-py`](https://github.com/PublicDotCom/publicdotcom-py) SDK.
