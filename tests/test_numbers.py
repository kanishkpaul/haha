import pytest

from career_tailor.numbers import extract_numbers, is_strong, is_unique, tokens


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IndicTrans2 preserved 98.9% versus 6.0%", ["98.9%", "6.0%"]),
        ("Detected 54/55 convulsive seizures", ["54/55"]),
        ("fits all 4,334 transitions but scores 0/168", ["4,334", "0/168"]),
        ("1.05–2.2× forward-throughput gains from 4K tokens", ["1.05", "2.2×", "4K"]),
        ("Top-250 team finish; macro F0.5 0.988216", ["250", "0.988216"]),
        ("replaced layers in Gemma-4-E2B and Llama-3.2-1B", ["1B"]),
        ("G0–G3 verified; SIH26171; Qwen3-VL 8B; multilingual-e5-small", ["8B"]),
        ("a sub-512 MiB footprint, peak RSS around 503 MB", ["512 MiB", "503 MB"]),
        ("₹20,000 commitment from a ₹5,000 budget", ["₹20,000", "₹5,000"]),
        ("approximately 7.8 s CPU versus 1.1 s Metal", ["7.8 s", "1.1 s"]),
        ("PPL 20.001→5.588 at 1,024 characters", ["20.001", "5.588", "1,024"]),
        ("100+ developers in eight hours on 4-bit models", ["100", "4"]),
        ("Upward:downward 58.7:1", ["58.7", "1"]),
        ("Amazon ML Challenge 2026 at 250 Hz", ["2026", "250 Hz"]),
    ],
)
def test_extract_numbers(text, expected):
    assert [m.text for m in extract_numbers(text)] == expected


def test_metric_values_units_and_years():
    pct, frac, year = extract_numbers("98.9% of 54/55 in 2026")
    assert (pct.value, pct.unit) == (98.9, "%")
    assert frac.value == pytest.approx(54 / 55)
    assert year.kind == "year" and pct.kind == "number"


def test_tokens_normalise_and_keep_fractions_whole():
    assert tokens("4,334/4,334 fit; 0/168 contrasts in 2026") == {"4334/4334", "0/168"}
    assert tokens("12K frames and 4,334 rows") == {"12000", "4334"}
    assert "2026" in tokens("in 2026", years=True)


@pytest.mark.parametrize(
    ("token", "strong", "unique"),
    [("0.988216", True, True), ("98.9", True, True), ("6.0", True, False), ("54/55", True, False),
     ("56000", True, True), ("250", True, False), ("16", False, False)],
)  # fmt: skip
def test_strength(token, strong, unique):
    assert (is_strong(token), is_unique(token)) == (strong, unique)
