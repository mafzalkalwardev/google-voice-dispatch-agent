from src.opening_pool import pick_opening, random_curated


def test_pick_opening_varies_by_phone():
    a = pick_opening("+15551111111", "Tony", "Indus Transports LLC")
    b = pick_opening("+15552222222", "Tony", "Indus Transports LLC")
    assert a and b
    assert "Tony" in a


def test_random_curated():
    line = random_curated("Tony", "Indus LLC")
    assert "Tony" in line
