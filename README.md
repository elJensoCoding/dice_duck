# Dice Duck 🎲🦆

Manipulationssicherer, **nachweisbar fairer** Würfelbecher mit REST-API, gebaut als erste
Komponente für *Dungeon Duck* (LLM-gemeistertes Pen & Paper im Chatroom).

## Warum?

LLMs würfeln nicht zufällig: Sie bevorzugen bestimmte Zahlen und erfinden gern Ergebnisse,
die zur Geschichte passen. Deshalb würfelt bei Dungeon Duck **nur dieser Server**, und das so,
dass niemand schummeln kann, ohne dass es auffällt. Das gilt auch für den Betreiber.

| Angriff | Schutz |
|---|---|
| LLM erfindet oder schönt Ergebnisse | Nur Würfe mit gültiger Server-Signatur zählen; der Chat zeigt die Server-Antwort an, nicht den LLM-Text |
| So lange würfeln, bis es passt | Jeder Wurf bekommt eine fortlaufende Nonce, landet im Log und ist für alle sichtbar |
| Würfe nachträglich ändern oder löschen | Hash-Kette und Ed25519-Signatur pro Wurf, `head_hash` für das Ende der Kette |
| Betreiber wählt günstige Seeds | Commit-Reveal: `SHA256(server_seed)` wird vorab veröffentlicht, und Spieler-Beiträge fließen in den `client_seed` ein |
| Schlechter Zufall oder Verzerrung | 256-Bit-Seed aus dem OS-CSPRNG, HMAC-SHA256-Strom, Rejection Sampling, Chi-Quadrat-Tests |

## Ablauf einer Session

```
1. POST /sessions                      → id, server_seed_hash   (Commitment)
2. POST /sessions/{id}/seed-contributions  {player, value}       (jeder Spieler, vor dem 1. Wurf)
3. POST /sessions/{id}/rolls           {expr, actor, reason}     → signierter Wurf
   …
4. POST /sessions/{id}/reveal          → server_seed aufgedeckt, Session geschlossen
5. GET  /sessions/{id}/verify          → Server-seitige Prüfung
   GET  /sessions/{id}/export          → Bundle zum Selbstprüfen:  dice-duck-verify export.json
```

Weitere Endpunkte: `GET /sessions/{id}`, `GET /sessions/{id}/rolls[/{nonce}]`,
`GET /public-key`, `GET /llm-tool` (fertige Tool-Definition für Function Calling).
Interaktive Doku unter `/docs`.

Wurf Nummer `n` wird aus `HMAC-SHA256(server_seed, "{client_seed}:{n}:{block}")` berechnet.
Mit dem aufgedeckten Seed lässt sich also jedes Ergebnis exakt nachrechnen.

## Würfelnotation

| Ausdruck | Bedeutung |
|---|---|
| `1d20+5`, `d20` | Standardwurf mit Modifikator |
| `d%` | W100 |
| `2d20kh1` / `2d20kl1` | Vorteil / Nachteil (höchsten / niedrigsten behalten) |
| `4d6dl1` | 4W6, niedrigsten verwerfen |
| `3d6!` | explodierend (bei Maximalwurf nachwürfeln und addieren, pro Würfel) |
| `5d10>=7` | Würfelpool: Anzahl Erfolge |
| `1d8+2d6-1` | beliebig kombinierbar |

Grenzen: max. 1000 Würfel pro Term, 2000 insgesamt, 10 000 Seiten, 20 Terme, 100 Explosionen pro Würfel.

## Starten

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m dice_duck          # http://127.0.0.1:8077/docs
.venv/Scripts/python -m pytest
```

Konfiguration per Umgebungsvariablen: `DICE_DUCK_DB` (Standard `data/dice_duck.sqlite3`) und
`DICE_DUCK_KEY` (Standard `data/signing_key.pem`, wird beim ersten Start erzeugt, **geheim halten**).

## Einbindung ins LLM

`GET /llm-tool` liefert die Tool-Definition `roll_dice`. Der Dungeon-Duck-Orchestrator führt den
Tool-Call gegen `POST /sessions/{id}/rolls` aus und zeigt die **Server-Antwort** (mit `roll_id`)
direkt im Chat an. Das LLM bekommt das Ergebnis erst danach als Tool-Result und erzählt weiter.

## Noch offen

- **Authentifizierung:** `actor` ist aktuell frei wählbar, und jeder mit der Session-ID kann `reveal` aufrufen.
  Das kommt mit der Dungeon-Duck-Integration (API-Keys bzw. Rollen GM, Spieler, Orchestrator).
- **Spieler sollten ihren Seed-Beitrag wirklich zufällig wählen.** Ohne Beiträge ist der
  `client_seed` ein fester Wert, und dann hängt die Fairness allein am Server.
- Verdeckte Würfe des Meisters (Ergebnis erst später sichtbar) sind noch nicht umgesetzt.
