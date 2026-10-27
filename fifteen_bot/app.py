"""Local web server: JSON API for the dashboard plus the static front end."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional

import httpx
import keyring
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from keyring.errors import KeyringError
from public_api_sdk import APIError, AuthenticationError
from pydantic import BaseModel, Field

from .engine import Engine
from .store import Store
from .strategy import Strategy

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FIFTEEN_BOT_DATA", ROOT / "data"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
KEYRING_SERVICE = "15-minute-bot"
KEYRING_USER = "public-api-secret-key"

store = Store(DATA_DIR / "bot.db")
engine = Engine(store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(engine.run())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await engine.disconnect()


app = FastAPI(title="15-Minute Bot", lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Only answer this machine's own browser tab.

    The Host check stops DNS-rebinding; the Origin check stops other websites
    from sending requests (e.g. "switch to live") to this server.
    """
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    origin = request.headers.get("origin")
    if request.method != "GET" and origin is not None:
        origin_host = origin.split("://", 1)[-1].split(":")[0]
        if origin_host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        # Revalidate the page's files on every load, so after an update the browser never
        # mixes a cached old index.html with a new app.js.
        response.headers["Cache-Control"] = "no-cache"
    return response


# --- session -----------------------------------------------------------------


def _saved_key() -> Optional[str]:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except KeyringError:
        return None


class ConnectBody(BaseModel):
    api_key: Optional[str] = None
    use_saved: bool = False
    remember: bool = False


@app.get("/api/session")
async def session():
    return {
        "connected": engine.client is not None,
        "account_id": engine.account_id,
        "accounts": [{"id": a.account_id, "type": a.account_type.value} for a in engine.accounts],
        "has_saved_key": _saved_key() is not None,
        # Installs set up before the wizard existed count as onboarded once they have markets.
        "onboarded": store.get_setting("onboarded") == "1" or bool(store.list_markets()),
    }


@app.post("/api/connect")
async def connect(body: ConnectBody):
    api_key = _saved_key() if body.use_saved else (body.api_key or "").strip()
    if not api_key:
        raise HTTPException(400, "Enter your Public API secret key")
    try:
        await engine.connect(api_key)
    except AuthenticationError:
        raise HTTPException(
            401,
            "Public didn't accept that key. Check that you copied the whole key "
            "and that it hasn't been deleted in Public's API settings.",
        )
    except APIError as exc:
        raise HTTPException(502, f"Public returned an error ({exc}). Please try again in a moment.")
    except httpx.TransportError:
        raise HTTPException(502, "Couldn't reach Public. Check your internet connection and try again.")
    if body.remember and not body.use_saved:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, api_key)
        except KeyringError as exc:
            logger.warning("could not save key to keychain: %s", exc)
    return await session()


class AccountBody(BaseModel):
    account_id: str


@app.post("/api/account")
async def select_account(body: AccountBody):
    try:
        engine.select_account(body.account_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return await session()


class DisconnectBody(BaseModel):
    forget_key: bool = False


@app.post("/api/disconnect")
async def disconnect(body: DisconnectBody):
    await engine.disconnect()
    if body.forget_key:
        with suppress(KeyringError):
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    return await session()


# --- engine ------------------------------------------------------------------


class EngineBody(BaseModel):
    running: bool


@app.post("/api/engine")
async def set_engine(body: EngineBody):
    try:
        engine.set_running(body.running)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return engine.snapshot()


@app.get("/api/state")
async def state():
    return engine.snapshot()


@app.get("/api/trades")
async def trades(series: Optional[str] = None, limit: int = 200):
    return store.trades(series, min(max(limit, 1), 1000))


# --- markets -----------------------------------------------------------------


def _require_client() -> None:
    if engine.client is None:
        raise HTTPException(409, "Not connected to Public")


@app.get("/api/markets/available")
async def available_markets():
    _require_client()
    try:
        return await engine.available_markets()
    except APIError as exc:
        raise HTTPException(502, f"Public API error: {exc}")


class AddMarketBody(BaseModel):
    series: str
    name: str
    category: Optional[str] = None


@app.post("/api/markets")
async def add_market(body: AddMarketBody):
    store.add_market(body.series, body.name, body.category)
    return engine.snapshot()


class UpdateMarketBody(BaseModel):
    enabled: Optional[bool] = None
    strategy: Optional[Strategy] = None


@app.put("/api/markets/{series}")
async def update_market(series: str, body: UpdateMarketBody):
    if store.get_market(series) is None:
        raise HTTPException(404, "Market not selected")
    store.update_market(series, enabled=body.enabled, strategy=body.strategy)
    return engine.snapshot()


class BulkStrategyBody(BaseModel):
    series: list[str]
    strategy: Strategy


@app.post("/api/markets/strategy")
async def apply_strategy(body: BulkStrategyBody):
    for series in body.series:
        store.update_market(series, strategy=body.strategy)
    return engine.snapshot()


class OnboardingBody(BaseModel):
    series: list[str] = Field(min_length=1)
    strategy: Strategy
    start: bool = False


@app.post("/api/onboarding")
async def finish_onboarding(body: OnboardingBody):
    """Save the setup wizard's choices in one go."""
    _require_client()
    if engine.account_id is None:
        raise HTTPException(400, "Choose an account first")
    known = {m["series"]: m for m in await engine.available_markets()}
    unknown = [s for s in body.series if s not in known]
    if unknown:
        raise HTTPException(400, f"Unknown market: {', '.join(unknown)}")
    for series in body.series:
        store.add_market(series, known[series]["name"], known[series]["category"])
        store.update_market(series, enabled=True, strategy=body.strategy)
    store.set_setting("onboarded", "1")
    if body.start:
        engine.set_running(True)
    return engine.snapshot()


@app.delete("/api/markets/{series}")
async def remove_market(series: str):
    store.remove_market(series)
    engine.windows.pop(series, None)
    return engine.snapshot()


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
