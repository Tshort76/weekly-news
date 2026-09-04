from pathlib import Path

import pytest

from digest.lens.compile import compile_lens
from digest.lens.schema import Example, Kinds, Level, LensSpec

PRESET = Path(__file__).parent.parent / "lenses" / "architecture-of-rule.toml"
RUBRIC = Path(__file__).parent.parent / "prompts" / "rubric.md"


def spec(**overrides) -> LensSpec:
    base = dict(
        name="a lens",
        about="a change in something",
        not_about="who is winning",
        fit3=Level(lead="Top."),
        fit2=Level(lead="Middle."),
        fit1=Level(lead="Bottom."),
        never=("sport",),
        kinds=Kinds(core="core", core_gloss="the core", adjacent="near", adjacent_gloss="nearby"),
        mechanism_examples=(),
    )
    base.update(overrides)
    return LensSpec(**base)


def words(text: str) -> str:
    """Line breaks are the compiler's business; the words are the lens."""
    return " ".join(text.split())


def test_the_shipped_preset_compiles_to_the_hand_written_rubric():
    assert words(compile_lens(LensSpec.from_toml(PRESET))) == words(RUBRIC.read_text())


def test_every_section_the_pipeline_depends_on_is_present_and_in_order():
    out = compile_lens(LensSpec.from_toml(PRESET))
    heads = ["LENS:", "Score FIT", "3 — ", "2 — ", "1 — ", "0 — ", "KIND:", "NOVELTY",
             "MECHANISM:", "Bias notes:"]
    found = [out.index(h) for h in heads]
    assert found == sorted(found)


@pytest.mark.parametrize(
    "examples, expected",
    [
        ((Example("one"), Example("two")), "one, two"),
        ((Example("one", "a, b"), Example("two")), "one (a, b); two"),
    ],
)
def test_examples_take_semicolons_only_when_a_comma_would_be_ambiguous(examples, expected):
    out = compile_lens(spec(fit3=Level(lead="Top.", examples=examples)))
    assert f"Examples: {expected}." in words(out)


def test_a_lead_ending_in_a_colon_runs_straight_into_its_examples():
    out = words(compile_lens(spec(fit2=Level(lead="Movement:", examples=(Example("a tariff"),)))))
    assert "2 — Movement: a tariff." in out and "Examples" not in out


def test_the_fit_one_exemption_survives_compilation():
    out = words(compile_lens(spec(fit1=Level(lead="Contest:", unless="it explains a consequence"))))
    assert "1 — Contest: — UNLESS it explains a consequence." in out


def test_kind_words_are_padded_so_the_glosses_line_up():
    out = compile_lens(spec(kinds=Kinds(core="architecture", core_gloss="how",
                                        adjacent="contest", adjacent_gloss="who")))
    assert "  architecture — how" in out and "  contest      — who" in out


def test_a_lens_with_no_places_of_interest_gets_no_bias_note():
    assert "Bias notes" not in compile_lens(spec())


def test_a_note_is_never_lost_between_the_file_and_the_markdown():
    loaded = LensSpec.from_toml(PRESET)
    assert "(fiscal, military, administrative)" in compile_lens(loaded)
