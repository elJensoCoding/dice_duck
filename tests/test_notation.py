import pytest

from dice_duck.notation import (
    ConstTerm,
    DiceTerm,
    NotationError,
    evaluate,
    normalize,
    parse,
)


class FixedRng:
    """Liefert vorgegebene Würfelwerte (1-basiert) in Reihenfolge."""

    def __init__(self, faces):
        self.faces = list(faces)

    def randbelow(self, n):
        v = self.faces.pop(0)
        assert 1 <= v <= n
        return v - 1


@pytest.mark.parametrize(
    "expr, normalized",
    [
        ("d20", "1d20"),
        ("2D6 + 3", "2d6+3"),
        ("d%", "1d100"),
        ("2d20kh", "2d20kh1"),
        ("4d6dl1", "4d6dl1"),
        ("5d10>=7", "5d10>=7"),
        ("3d6!", "3d6!"),
        ("-1d4+1d8-2", "-1d4+1d8-2"),
        ("10d6!kh3>4", "10d6!kh3>4"),
    ],
)
def test_parse_normalize(expr, normalized):
    assert normalize(parse(expr)) == normalized


@pytest.mark.parametrize(
    "expr",
    ["", "   ", "5", "2d", "d0", "2d6+", "2d6 3", "2 d6", "d20 kh1", "2x6", "1001d6", "d6kh2", "2d6dl2",
     "1d1!", "d6k", "abc", "1d6" * 100, "1d20+" + "9" * 10],
)
def test_parse_rejects(expr):
    with pytest.raises(NotationError):
        parse(expr)


def test_parse_terms():
    t = parse("2d20kh1+5")
    assert t == [DiceTerm(1, 2, 20, False, "kh", 1), ConstTerm(1, 5)]


def test_evaluate_sum_and_constant():
    r = evaluate(parse("2d6+3"), FixedRng([4, 5]))
    assert r["total"] == 12
    assert [d["value"] for d in r["terms"][0]["dice"]] == [4, 5]


def test_advantage_and_disadvantage():
    adv = evaluate(parse("2d20kh1"), FixedRng([7, 15]))
    assert adv["total"] == 15
    assert [d["kept"] for d in adv["terms"][0]["dice"]] == [False, True]
    dis = evaluate(parse("2d20kl1"), FixedRng([7, 15]))
    assert dis["total"] == 7


def test_drop_lowest():
    r = evaluate(parse("4d6dl1"), FixedRng([3, 6, 1, 5]))
    assert r["total"] == 14


def test_keep_ties_are_deterministic():
    r = evaluate(parse("3d6kh1"), FixedRng([6, 6, 2]))
    assert r["total"] == 6
    assert [d["kept"] for d in r["terms"][0]["dice"]] == [False, True, False]


def test_exploding():
    r = evaluate(parse("2d6!"), FixedRng([6, 6, 2, 3]))
    dice = r["terms"][0]["dice"]
    assert dice[0]["faces"] == [6, 6, 2]
    assert dice[1]["faces"] == [3]
    assert r["total"] == 17


def test_explosion_cap():
    r = evaluate(parse("1d2!"), FixedRng([2] * 200))
    assert len(r["terms"][0]["dice"][0]["faces"]) == 101


def test_success_counting():
    r = evaluate(parse("5d10>=7"), FixedRng([7, 3, 10, 6, 9]))
    assert r["total"] == 3
    assert [d["success"] for d in r["terms"][0]["dice"]] == [True, False, True, False, True]


def test_negative_term():
    r = evaluate(parse("1d20-1d4"), FixedRng([10, 3]))
    assert r["total"] == 7
