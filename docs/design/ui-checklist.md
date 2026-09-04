# Before a release: the things a test cannot check

The suite drives every route with `TestClient` and asserts on rendered text. That
covers what the pages *say*. It cannot see what they *look like*, and it never
runs a real week. So this list gets walked by hand, on a real machine, before a
release goes out.

Fifteen minutes if nothing is wrong.

## Setup, on a machine with no config

1. `DIGEST_HOME=/tmp/fresh digest open` — lands on setup, not on an error.
2. With Ollama running: the models it found are named, and the recommendation
   says what was measured about it rather than that it is good.
3. With Ollama stopped: the page says "installed but not running", not "not
   installed". Stop it and check, because the two need different advice.
4. Pick a preset, save. The lens file appears; the feed list is the preset's.
5. An uncalibrated preset is visibly labelled as one.

## A run

6. Press *Run this week*. The progress list grows without the page being
   reloaded by hand.
7. **Close the tab. Open it again.** The list is still there and still growing —
   this is the thing the job runner exists for, and the only way to see it fail
   is to try it.
8. Press *Stop*. The run ends within one entry, the page says stopped rather than
   failed, and nothing was marked as seen.
9. Start a run, then run `digest run` in a terminal. It refuses, and says why.

## Reading a week

10. The edition reads as prose, not as a data dump. Carried entries say whose
    words they are.
11. The audit panel is beside it and its reasons are legible sentences.
12. There is no button anywhere that keeps or drops a single item. If one has
    appeared, something has gone wrong with the design, not the code.

## The lens

13. Edit `lens.md` in an editor. The form shows the banner and the diff shows
    your line. Save the form; the banner clears.
14. Add an example from the check-the-lens screen. It lands in the right level.

## Scheduling

15. `digest schedule on`, then check the file it wrote with `digest schedule
    show`. On macOS: `launchctl list io.digest.weekly` returns 0.
16. `digest schedule off` removes it.

## Both themes and a narrow window

17. Switch the OS to dark mode and reload. Nothing is dark text on a dark ground.
18. Drag the window to phone width. The sidebar stacks, and nothing scrolls
    sideways.

## Print

19. `digest run --pdf` on a machine with Chrome, and on one without. The second
    says "install Chrome or Edge" and still writes everything else.
