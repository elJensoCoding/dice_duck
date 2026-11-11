"""Würfelnotation: Parser und Auswertung.

Unterstützte Syntax (Groß-/Kleinschreibung und Leerzeichen egal):

    XdY        X Würfel mit Y Seiten (X optional, Standard 1)      3d6, d20
    d%         entspricht d100
    !          explodierend: bei Maximalwurf wird nachgewürfelt
               und addiert (pro Würfel, max. 100 Explosionen)      d6!
    khN / klN  die N höchsten / niedrigsten Würfel behalten        2d20kh1  (Vorteil)
    dhN / dlN  die N höchsten / niedrigsten Würfel verwerfen       4d6dl1
    >=T >T     Erfolge zählen: Anzahl (behaltener) Würfel,         5d10>=7
    <=T <T     die die Bedingung erfüllen
    + / -      Terme verknüpfen, auch mit Konstanten               1d8+2d6+3

Die Auswertung ist deterministisch bezogen auf die Zufallsquelle. Das ist die
Voraussetzung dafür, dass jeder Wurf später unabhängig nachgerechnet werden kann.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from typing import Any, Protocol

MAX_EXPR_LEN = 200
MAX_TERMS = 20
MAX_DICE_PER_TERM = 1000
MAX_TOTAL_DICE = 2000
MAX_SIDES = 10_000
MAX_CONSTANT = 1_000_000
MAX_EXPLOSIONS_PER_DIE = 100


class NotationError(ValueError):
    """Ungültiger Würfelausdruck."""


class RandomSource(Protocol):
    def randbelow(self, n: int) -> int:
        """Gleichverteilte Ganzzahl in [0, n)."""


_COMPARATORS = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
}


@dataclass(frozen=True)
class DiceTerm:
    sign: int
    count: int
    sides: int
    explode: bool = False
    keep_mode: str | None = None  # kh | kl | dh | dl
    keep_n: int | None = None
    cmp: str | None = None  # >= | > | <= | <
    target: int | None = None

    @property
    def notation(self) -> str:
        s = f"{self.count}d{self.sides}"
        if self.explode:
            s += "!"
        if self.keep_mode:
            s += f"{self.keep_mode}{self.keep_n}"
        if self.cmp:
            s += f"{self.cmp}{self.target}"
        return s


@dataclass(frozen=True)
class ConstTerm:
    sign: int
    value: int

    @property
    def notation(self) -> str:
        return str(self.value)


Term = DiceTerm | ConstTerm

_DICE_RE = re.compile(
    r"(?P<count>\d+)?d(?P<sides>\d+|%)"
    r"(?P<explode>!)?"
    r"(?:(?P<keep>kh|kl|dh|dl)(?P<keep_n>\d+)?)?"
    r"(?:(?P<cmp>>=|<=|>|<)(?P<target>\d+))?"
)
_CONST_RE = re.compile(r"\d+")


def parse(expr: str) -> list[Term]:
    """Zerlegt einen Würfelausdruck in Terme oder wirft NotationError."""
    if not isinstance(expr, str):
        raise NotationError("Ausdruck muss ein String sein")
    if len(expr) > MAX_EXPR_LEN:
        raise NotationError(f"Ausdruck zu lang (max. {MAX_EXPR_LEN} Zeichen)")
    lowered = expr.lower()
    # Leerzeichen nur um Operatoren erlauben, sonst würde z. B. "2d6 3" still zu "2d63"
    if re.search(r"[^\s+\-]\s+[^\s+\-]", lowered):
        raise NotationError("Leerzeichen sind nur um + und - erlaubt")
    s = re.sub(r"\s+", "", lowered)
    if not s:
        raise NotationError("Leerer Ausdruck")

    terms: list[Term] = []
    pos = 0
    while pos < len(s):
        sign = 1
        if s[pos] in "+-":
            sign = -1 if s[pos] == "-" else 1
            pos += 1
            if pos >= len(s):
                raise NotationError("Ausdruck endet mit einem Operator")
        elif terms:
            raise NotationError(f"Operator (+/-) erwartet bei: {s[pos:]!r}")

        m = _DICE_RE.match(s, pos)
        if m:
            term: Term = _build_dice(sign, m)
        else:
            m = _CONST_RE.match(s, pos)
            if not m:
                raise NotationError(f"Unverständlich ab: {s[pos:]!r}")
            value = int(m.group())
            if value > MAX_CONSTANT:
                raise NotationError(f"Konstante zu groß (max. {MAX_CONSTANT})")
            term = ConstTerm(sign, value)
        pos = m.end()
        terms.append(term)
        if len(terms) > MAX_TERMS:
            raise NotationError(f"Zu viele Terme (max. {MAX_TERMS})")

    dice_terms = [t for t in terms if isinstance(t, DiceTerm)]
    if not dice_terms:
        raise NotationError("Ausdruck enthält keine Würfel")
    if sum(t.count for t in dice_terms) > MAX_TOTAL_DICE:
        raise NotationError(f"Zu viele Würfel insgesamt (max. {MAX_TOTAL_DICE})")
    return terms


def _build_dice(sign: int, m: re.Match[str]) -> DiceTerm:
    count = int(m["count"]) if m["count"] else 1
    sides = 100 if m["sides"] == "%" else int(m["sides"])
    if not 1 <= count <= MAX_DICE_PER_TERM:
        raise NotationError(f"Würfelanzahl muss zwischen 1 und {MAX_DICE_PER_TERM} liegen")
    if not 1 <= sides <= MAX_SIDES:
        raise NotationError(f"Seitenzahl muss zwischen 1 und {MAX_SIDES} liegen")

    explode = bool(m["explode"])
    if explode and sides < 2:
        raise NotationError("Explodierende Würfel brauchen mindestens 2 Seiten")

    keep_mode = m["keep"]
    keep_n = None
    if keep_mode:
        keep_n = int(m["keep_n"]) if m["keep_n"] else 1
        if keep_mode in ("kh", "kl") and not 1 <= keep_n <= count:
            raise NotationError(f"{keep_mode}{keep_n}: muss zwischen 1 und {count} Würfel behalten")
        if keep_mode in ("dh", "dl") and not 1 <= keep_n < count:
            raise NotationError(f"{keep_mode}{keep_n}: muss zwischen 1 und {count - 1} Würfel verwerfen")

    cmp = m["cmp"]
    target = int(m["target"]) if cmp else None
    return DiceTerm(sign, count, sides, explode, keep_mode, keep_n, cmp, target)


def normalize(terms: list[Term]) -> str:
    """Kanonische Schreibweise, z. B. 'd20 + 5' -> '1d20+5'."""
    out = []
    for i, t in enumerate(terms):
        if t.sign < 0:
            out.append("-")
        elif i > 0:
            out.append("+")
        out.append(t.notation)
    return "".join(out)


def evaluate(terms: list[Term], rng: RandomSource) -> dict[str, Any]:
    """Würfelt alle Terme mit der gegebenen Zufallsquelle aus."""
    out_terms: list[dict[str, Any]] = []
    total = 0
    for t in terms:
        if isinstance(t, ConstTerm):
            out_terms.append({"type": "constant", "sign": t.sign, "value": t.value})
            total += t.sign * t.value
            continue

        dice: list[dict[str, Any]] = []
        for _ in range(t.count):
            faces = [rng.randbelow(t.sides) + 1]
            if t.explode:
                while faces[-1] == t.sides and len(faces) <= MAX_EXPLOSIONS_PER_DIE:
                    faces.append(rng.randbelow(t.sides) + 1)
            dice.append({"faces": faces, "value": sum(faces), "kept": True})

        if t.keep_mode:
            assert t.keep_n is not None
            # aufsteigend nach Wert, bei Gleichstand nach Position -> deterministisch
            order = sorted(range(len(dice)), key=lambda i: (dice[i]["value"], i))
            n = t.keep_n
            keep = {
                "kh": order[-n:],
                "kl": order[:n],
                "dh": order[:-n],
                "dl": order[n:],
            }[t.keep_mode]
            keep_set = set(keep)
            for i, d in enumerate(dice):
                d["kept"] = i in keep_set

        kept_values = [d["value"] for d in dice if d["kept"]]
        if t.cmp:
            op = _COMPARATORS[t.cmp]
            for d in dice:
                d["success"] = bool(d["kept"] and op(d["value"], t.target))
            subtotal = sum(1 for d in dice if d["success"])
        else:
            subtotal = sum(kept_values)

        out_terms.append(
            {
                "type": "dice",
                "notation": t.notation,
                "sign": t.sign,
                "dice": dice,
                "subtotal": subtotal,
            }
        )
        total += t.sign * subtotal

    return {"terms": out_terms, "total": total}
