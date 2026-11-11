from collections import Counter

import pytest

from dice_duck.fairness import HmacStream, client_seed_from, commitment, new_server_seed

# Kritische Chi-Quadrat-Werte bei p = 0.001 (sehr unwahrscheinlich, dass ein faires
# System sie überschreitet; ein Test schlägt also praktisch nie zufällig fehl).
CHI2_CRIT_P001 = {5: 20.515, 19: 43.820, 99: 148.230}


def chi_square(counts: Counter, sides: int, n: int) -> float:
    expected = n / sides
    return sum((counts.get(face, 0) - expected) ** 2 / expected for face in range(sides))


def test_deterministic_for_same_inputs():
    seed = new_server_seed()
    a = HmacStream(seed, "c", 0)
    b = HmacStream(seed, "c", 0)
    assert [a.randbelow(20) for _ in range(100)] == [b.randbelow(20) for _ in range(100)]


def test_differs_by_nonce_and_client_seed():
    seed = new_server_seed()
    base = [HmacStream(seed, "c", 0).randbelow(1_000_000) for _ in range(1)]
    assert base != [HmacStream(seed, "c", 1).randbelow(1_000_000)]
    assert base != [HmacStream(seed, "d", 0).randbelow(1_000_000)]


def test_range():
    s = HmacStream(new_server_seed(), "x", 0)
    for n in (1, 2, 3, 6, 7, 20, 100, 10_000):
        assert all(0 <= s.randbelow(n) < n for _ in range(500))
    with pytest.raises(ValueError):
        s.randbelow(0)


@pytest.mark.parametrize("sides", [6, 20, 100])
def test_uniform_distribution(sides):
    n = 60_000
    s = HmacStream(new_server_seed(), "chi", 0)
    counts = Counter(s.randbelow(sides) for _ in range(n))
    assert chi_square(counts, sides, n) < CHI2_CRIT_P001[sides - 1]


def test_commitment_and_client_seed():
    seed = bytes(32)
    assert commitment(seed) == "66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"
    # Längenpräfix verhindert Mehrdeutigkeit: ["ab","c"] != ["a","bc"]
    assert client_seed_from(["ab", "c"]) != client_seed_from(["a", "bc"])
    assert client_seed_from(["a", "b"]) != client_seed_from(["b", "a"])
