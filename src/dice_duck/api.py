"""REST-API des Würfelbechers.

Start:  uvicorn dice_duck.api:create_app --factory
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .cup import ConflictError, DiceCup, NotFoundError
from .notation import MAX_EXPR_LEN, NotationError
from .signing import Signer

DESCRIPTION = """
Manipulationssicherer, **nachweisbar fairer** Würfelbecher für LLM-gemeisterte Runden.

* Zufall: CSPRNG-Seed + HMAC-SHA256, Rejection Sampling (keine Modulo-Verzerrung)
* Commit-Reveal: `server_seed_hash` wird vorab veröffentlicht, der Seed erst am Ende aufgedeckt
* Spieler steuern vor dem ersten Wurf eigene Seed-Beiträge bei
* Jeder Wurf ist Ed25519-signiert und hash-verkettet; Lücken oder Änderungen fallen auf
"""


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ContributionIn(BaseModel):
    player: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=256, description="Beliebiger Zufallstext des Spielers")


class RollIn(BaseModel):
    expr: str = Field(min_length=1, max_length=MAX_EXPR_LEN, examples=["2d20kh1+5"])
    actor: str = Field(min_length=1, max_length=64, examples=["llm-gm"])
    reason: str | None = Field(default=None, max_length=500, examples=["Angriff auf den Ork"])


LLM_TOOL = {
    "name": "roll_dice",
    "description": (
        "Würfelt fair und manipulationssicher über den Dice-Duck-Server. Verwende IMMER dieses "
        "Tool für jeden Würfelwurf und erfinde niemals Ergebnisse. Notation: XdY, d%, "
        "! (explodierend), khN/klN (höchste/niedrigste N behalten, z. B. 2d20kh1 = Vorteil), "
        "dhN/dlN (verwerfen, z. B. 4d6dl1), >=T (Erfolge zählen, z. B. 5d10>=7), +/- Konstanten."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expr": {"type": "string", "description": "Würfelausdruck, z. B. '1d20+5'"},
            "actor": {"type": "string", "description": "Wer würfelt (Figur oder Spieler)"},
            "reason": {"type": "string", "description": "Wofür gewürfelt wird"},
        },
        "required": ["expr", "actor", "reason"],
    },
}


def create_app(db_path: str | None = None, key_path: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("DICE_DUCK_DB", "data/dice_duck.sqlite3")
    key_path = key_path or os.environ.get("DICE_DUCK_KEY", "data/signing_key.pem")
    cup = DiceCup(db_path, Signer.load_or_create(key_path))

    app = FastAPI(title="Dice Duck 🎲🦆", version=__version__, description=DESCRIPTION)
    app.state.cup = cup

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotationError)
    async def _notation(_: Request, exc: NotationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": f"Ungültige Würfelnotation: {exc}"})

    # Sync-Handler laufen im Threadpool. DiceCup serialisiert Schreibzugriffe per Lock.

    @app.get("/", tags=["info"])
    def info() -> dict[str, Any]:
        return {"service": "dice-duck", "version": __version__, "docs": "/docs"}

    @app.get("/public-key", tags=["info"])
    def public_key() -> dict[str, str]:
        return {"algorithm": "Ed25519", "encoding": "base64-raw", "public_key": cup.signer.public_key_b64}

    @app.get("/llm-tool", tags=["info"], summary="Tool-Definition für LLM Function Calling")
    def llm_tool() -> dict[str, Any]:
        return LLM_TOOL

    @app.post("/sessions", status_code=201, tags=["sessions"])
    def create_session(body: SessionCreate | None = None) -> dict[str, Any]:
        return cup.create_session(body.title if body else None)

    @app.get("/sessions/{sid}", tags=["sessions"])
    def get_session(sid: str) -> dict[str, Any]:
        return cup.get_session(sid)

    @app.post("/sessions/{sid}/seed-contributions", tags=["sessions"],
              summary="Spieler-Zufallsbeitrag (nur vor dem ersten Wurf)")
    def add_contribution(sid: str, body: ContributionIn) -> dict[str, Any]:
        return cup.add_contribution(sid, body.player, body.value)

    @app.post("/sessions/{sid}/rolls", status_code=201, tags=["rolls"])
    def roll(sid: str, body: RollIn) -> dict[str, Any]:
        return cup.roll(sid, body.expr, body.actor, body.reason)

    @app.get("/sessions/{sid}/rolls", tags=["rolls"])
    def list_rolls(sid: str) -> list[dict[str, Any]]:
        return cup.list_rolls(sid)

    @app.get("/sessions/{sid}/rolls/{nonce}", tags=["rolls"])
    def get_roll(sid: str, nonce: int) -> dict[str, Any]:
        return cup.get_roll(sid, nonce)

    @app.post("/sessions/{sid}/reveal", tags=["fairness"],
              summary="Session beenden und server_seed aufdecken")
    def reveal(sid: str) -> dict[str, Any]:
        return cup.reveal(sid)

    @app.get("/sessions/{sid}/verify", tags=["fairness"])
    def verify(sid: str) -> dict[str, Any]:
        return cup.verify(sid)

    @app.get("/sessions/{sid}/export", tags=["fairness"],
             summary="Alles zum unabhängigen Nachprüfen (dice-duck-verify)")
    def export(sid: str) -> dict[str, Any]:
        return cup.export(sid)

    return app
