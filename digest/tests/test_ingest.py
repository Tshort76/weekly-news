"""Feed-shape filtering. No network — is_promotional is a pure function."""

from __future__ import annotations

import pytest

from digest.ingest import is_promotional

# Real items from the 2026-W36 fetch. The first reached fit 2 and was written
# up as a Red Sea shipping story that none of its sources contained.
TRAILERS = [
    ("Middle East Dispatch: The return of the Gulf war",
     "Gregg Carlstrom, our Middle East correspondent, on the reasons for the recent skirmishes"),
    ("Bartleby newsletter: Teamwork doesn’t make the machines work",
     "Our columnist, Andrew Palmer, looks at whether AI agents are able to co-operate"),
    ("Analysing Africa newsletter: A five-point guide to being a billionaire",
     "John McDermott, chief Africa correspondent, provides some tongue-in-cheek advice"),
    ("The War Room newsletter: Let slip the dogs of war",
     "Richard Cockett, a senior editor, looks back at the history of animals in warfare"),
    ("FirstFT: KPMG warned Guggenheim unit over weak controls",
     "Also in today’s newsletter: Dutch gold bars and Google ads"),
    ("The Economist is hiring a Senior Audience Editor for newsletters",
     "We are offering a permanent contract in London"),
]

NEWS = [
    ("Solar overtakes coal as China’s biggest source of power capacity",
     "Installed solar is now nearly a third of the total, up from almost nothing."),
    ("Kevin Warsh tries being a normal central banker", "And markets like it"),
    ("Nepal flash floods", ""),
    ("Judge rejects bid to break up Google’s advertising arm",
     "The court ordered behavioural remedies instead of a forced sale."),
]


@pytest.mark.parametrize("title,blurb", TRAILERS)
def test_a_trailer_for_journalism_is_not_journalism(title, blurb):
    assert is_promotional(title, blurb)


@pytest.mark.parametrize("title,blurb", NEWS)
def test_a_story_survives_the_filter(title, blurb):
    assert not is_promotional(title, blurb)


def test_a_story_about_a_journalist_is_still_a_story():
    """"a Variety reporter" is what the story is about, not who wrote it. The
    filter keys on "our" and on seniority for exactly this reason."""
    assert not is_promotional(
        "UTA goes to war with Hollywood news giant",
        "The CEO of United Talent Agency is going after a Variety reporter.",
    )


def test_a_terse_blurb_is_not_a_trailer():
    """Thin is not the same as promotional. A one-line blurb still describes an
    event, and dropping those would cost real news."""
    assert not is_promotional("Yen gains against the dollar", "Traders expect a rate move.")
