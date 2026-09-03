from digest.models import Classified
from digest.selection import select

from .conftest import load_fixture, make_classified


def _week():
    return [Classified.from_dict(d) for d in load_fixture("classified_week.json")]


def test_fit_below_two_is_dropped():
    kept, dropped = select([make_classified(fit=0, novelty=0)], _cfg())
    assert kept == []
    assert "below threshold" in dropped[0].reason


def test_fit_one_survives_only_at_novelty_three():
    cfg = _cfg()
    assert select([make_classified(fit=1, novelty=3, kind="architecture")], cfg)[0]
    assert not select([make_classified(fit=1, novelty=2, kind="architecture")], cfg)[0]


def test_saga_rule_drops_a_repeat_mechanism_at_low_novelty():
    c = make_classified(fit=2, novelty=1, mechanism="developer presales fund construction")
    kept, dropped = select([c], _cfg(), prior_mechanisms=["developer pre-sales fund construction"])
    assert kept == []
    assert "saga" in dropped[0].reason


def test_saga_rule_spares_fit_three():
    c = make_classified(fit=3, novelty=1, mechanism="developer presales fund construction")
    kept, _ = select([c], _cfg(), prior_mechanisms=["developer presales fund construction"])
    assert len(kept) == 1


def test_saga_rule_spares_a_novel_item():
    c = make_classified(fit=2, novelty=3, mechanism="developer presales fund construction")
    kept, _ = select([c], _cfg(), prior_mechanisms=["developer presales fund construction"])
    assert len(kept) == 1


def test_balance_rule_caps_contest_items():
    cfg = _cfg(contest_share=0.20)
    kept, dropped = select(_week(), cfg)
    kinds = [c.kind for c in kept]
    assert kinds.count("contest") <= 0.20 * len(kinds)
    assert any("balance rule" in d.reason for d in dropped)


def test_balance_rule_iterates_as_the_denominator_shrinks():
    # Four architecture, four contest. A one-pass 20% cap would leave 4/8 -> drop
    # until 1.6 -> two dropped, still 2/6 = 33%. The rule has to keep going.
    items = [make_classified(fit=3, kind="architecture", item={"url": f"https://e.com/a{n}"}) for n in range(4)]
    items += [
        make_classified(fit=2, kind="contest", novelty=2, item={"url": f"https://e.com/c{n}"})
        for n in range(4)
    ]
    kept, _ = select(items, _cfg(contest_share=0.20))
    kinds = [c.kind for c in kept]
    assert kinds.count("contest") <= 0.20 * len(kinds)


def test_balance_rule_drops_the_lowest_fit_contest_first():
    high = make_classified(fit=3, kind="contest", item={"url": "https://e.com/high"})
    low = make_classified(fit=2, kind="contest", novelty=2, item={"url": "https://e.com/low"})
    # Ten items at a 15% cap allows one contest item: the higher-fit one stays.
    arch = [
        make_classified(fit=3, kind="architecture", item={"url": f"https://e.com/a{n}"})
        for n in range(8)
    ]
    kept, _ = select([high, low, *arch], _cfg(contest_share=0.15))
    contest_kept = [c for c in kept if c.kind == "contest"]
    assert [c.item.url for c in contest_kept] == ["https://e.com/high"]


def test_hard_cap_keeps_the_best_by_fit_times_weight():
    items = [
        make_classified(fit=3, item={"url": "https://e.com/1", "weight": 1.0}),
        make_classified(fit=3, item={"url": "https://e.com/2", "weight": 0.7}),
        make_classified(fit=2, item={"url": "https://e.com/3", "weight": 1.0}),
    ]
    kept, dropped = select(items, _cfg(max_items=2))
    assert [c.item.url for c in kept] == ["https://e.com/1", "https://e.com/2"]
    assert "cap" in dropped[0].reason


def test_every_dropped_item_carries_a_reason():
    _, dropped = select(_week(), _cfg())
    assert dropped and all(d.reason and d.title for d in dropped)


def _cfg(contest_share: float = 0.20, max_items: int = 60):
    from digest.config import Config, RunCfg

    return Config(run=RunCfg(contest_share=contest_share, max_items=max_items))
