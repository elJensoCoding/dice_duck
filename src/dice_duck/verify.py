"""Unabhängige Prüfung einer exportierten Session.

Kommt bewusst ohne Datenbank aus und braucht nur den Export
(``GET /sessions/{id}/export``). Jeder Spieler kann damit selbst nachrechnen:

    dice-duck-verify session_export.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any

from .fairness import HmacStream, client_seed_from, commitment
from .notation import NotationError, evaluate, normalize, parse
from .signing import canonical_json, verify_signature


def genesis_hash(session_id: str, server_seed_hash: str) -> str:
    return hashlib.sha256(f"dice-duck-genesis-v1:{session_id}:{server_seed_hash}".encode()).hexdigest()


def entry_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(record)).hexdigest()


def recompute(record: dict[str, Any], server_seed: bytes) -> dict[str, Any]:
    terms = parse(record["expr"])
    return evaluate(terms, HmacStream(server_seed, record["client_seed"], record["nonce"]))


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    session = bundle["session"]
    public_key = bundle["public_key"]
    sid = session["id"]
    seed_hash = session["server_seed_hash"]

    server_seed: bytes | None = None
    if session.get("server_seed"):
        server_seed = bytes.fromhex(session["server_seed"])
        if commitment(server_seed) != seed_hash:
            errors.append("Aufgedeckter server_seed passt nicht zum veröffentlichten Hash")
            server_seed = None

    expected_client_seed = client_seed_from([c["value"] for c in session.get("contributions", [])])
    if session.get("client_seed") != expected_client_seed:
        errors.append("client_seed passt nicht zu den Seed-Beiträgen")

    prev = genesis_hash(sid, seed_hash)
    rolls = bundle.get("rolls", [])
    for i, entry in enumerate(rolls):
        rec = entry["record"]
        tag = f"Wurf #{i}"
        if rec.get("nonce") != i:
            errors.append(f"{tag}: Nonce {rec.get('nonce')} statt {i} (Lücke oder Umsortierung)")
        if rec.get("session_id") != sid:
            errors.append(f"{tag}: falsche Session")
        if rec.get("server_seed_hash") != seed_hash:
            errors.append(f"{tag}: falscher server_seed_hash")
        if rec.get("client_seed") != expected_client_seed:
            errors.append(f"{tag}: falscher client_seed")
        if rec.get("prev_hash") != prev:
            errors.append(f"{tag}: Hash-Kette unterbrochen")
        h = entry_hash(rec)
        if h != entry.get("entry_hash"):
            errors.append(f"{tag}: entry_hash stimmt nicht (Eintrag verändert)")
        if not verify_signature(public_key, canonical_json(rec), entry.get("signature", "")):
            errors.append(f"{tag}: Signatur ungültig")
        if server_seed is not None:
            try:
                result = recompute(rec, server_seed)
                if result != rec.get("result"):
                    errors.append(f"{tag}: Ergebnis lässt sich nicht reproduzieren")
                if normalize(parse(rec["expr"])) != rec.get("normalized"):
                    errors.append(f"{tag}: normalisierter Ausdruck stimmt nicht")
            except (NotationError, KeyError) as e:
                errors.append(f"{tag}: nicht nachrechenbar ({e})")
        prev = h

    if session.get("roll_count") != len(rolls):
        errors.append(f"roll_count {session.get('roll_count')} ≠ {len(rolls)} exportierte Würfe")
    if session.get("head_hash") != prev:
        errors.append("head_hash passt nicht zum letzten Wurf (Würfe fehlen am Ende?)")

    return {
        "ok": not errors,
        "session_id": sid,
        "rolls_checked": len(rolls),
        "outcomes_recomputed": server_seed is not None,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prüft einen Dice-Duck-Session-Export.")
    ap.add_argument("export", help="JSON-Datei von GET /sessions/{id}/export ('-' für stdin)")
    args = ap.parse_args(argv)
    raw = sys.stdin.read() if args.export == "-" else open(args.export, encoding="utf-8").read()
    report = verify_bundle(json.loads(raw))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["ok"] and not report["outcomes_recomputed"]:
        print("Hinweis: server_seed noch nicht aufgedeckt, Ergebnisse wurden nicht nachgerechnet.",
              file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
