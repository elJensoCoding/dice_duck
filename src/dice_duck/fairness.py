"""Nachweisbar faire Zufallszahlen (Commit-Reveal mit HMAC-SHA256).

Ablauf:
1. Der Server erzeugt pro Session einen geheimen ``server_seed`` (32 Byte aus dem
   CSPRNG des Betriebssystems) und veröffentlicht sofort nur ``SHA256(server_seed)``.
2. Spieler steuern vor dem ersten Wurf eigene Zufallsbeiträge bei. Daraus entsteht
   der ``client_seed``. So kann der Server die Würfe nicht im Voraus auswählen.
3. Wurf Nummer ``nonce`` bezieht seine Zufallsbytes aus
   ``HMAC-SHA256(server_seed, f"{client_seed}:{nonce}:{block}")``.
4. Am Ende wird ``server_seed`` aufgedeckt, und jeder kann alle Würfe nachrechnen.

Ganzzahlen werden per Rejection Sampling gezogen, es gibt also keine Modulo-Verzerrung.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

SEED_BYTES = 32
_RANGE_BITS = 32
_RANGE = 1 << _RANGE_BITS


def new_server_seed() -> bytes:
    return secrets.token_bytes(SEED_BYTES)


def commitment(server_seed: bytes) -> str:
    return hashlib.sha256(server_seed).hexdigest()


def client_seed_from(contributions: list[str]) -> str:
    """Kombiniert Spielerbeiträge eindeutig (längenpräfixiert) zu einem Seed."""
    h = hashlib.sha256(b"dice-duck-client-seed-v1")
    for c in contributions:
        data = c.encode("utf-8")
        h.update(len(data).to_bytes(4, "big"))
        h.update(data)
    return h.hexdigest()


class HmacStream:
    """Deterministischer Zufallsstrom für genau einen Wurf."""

    def __init__(self, server_seed: bytes, client_seed: str, nonce: int) -> None:
        self._key = server_seed
        self._prefix = f"{client_seed}:{nonce}:".encode("ascii")
        self._block = 0
        self._buf = b""

    def _take(self, n: int) -> bytes:
        while len(self._buf) < n:
            msg = self._prefix + str(self._block).encode("ascii")
            self._buf += hmac.new(self._key, msg, hashlib.sha256).digest()
            self._block += 1
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def randbelow(self, n: int) -> int:
        if not 0 < n <= _RANGE:
            raise ValueError(f"n muss in (0, 2^{_RANGE_BITS}] liegen")
        if n == 1:
            return 0
        limit = _RANGE - (_RANGE % n)  # größtes Vielfaches von n <= 2^32
        while True:
            x = int.from_bytes(self._take(4), "big")
            if x < limit:
                return x % n
