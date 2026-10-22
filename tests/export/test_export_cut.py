from tglw.export.build import cut


def test_cut_whole_when_small():
    assert cut([10, 10, 10], [0, 1, 1], 100) == [(0, 3)]


def test_cut_at_strongest_boundaries_then_joins():
    # two chapters (strength 1 at index 2), the second with a subsection (strength 2 at index 3)
    tokens = [30, 30, 30, 30]
    strength = [0, 50, 1, 2]
    assert cut(tokens, strength, 60) == [(0, 2), (2, 4)]


def test_cut_descends_to_pages_and_keeps_big_chunk_whole():
    tokens = [50, 50, 500, 50]
    strength = [0, 50, 100, 50]
    ranges = cut(tokens, strength, 100)
    assert ranges == [(0, 2), (2, 3), (3, 4)]
    assert [x for r in ranges for x in range(*r)] == list(range(4))


def test_cut_joins_small_neighbours_across_boundaries():
    tokens = [10, 10, 10, 10, 10]
    strength = [0, 1, 1, 1, 1]
    assert cut(tokens, strength, 25) == [(0, 2), (2, 4), (4, 5)]
