"""Timing test: align's plan for the Psalm 1 demo must start each section where the English does.

Uses what align heard in the demo (tests/fixtures/demo_ps1.json, made by tests/record_demo.py),
so it needs no audio, ffmpeg or Whisper.
"""
import json, os

import pytest

from dub import cli

HERE = os.path.dirname(__file__)
TOLERANCE = 0.05   # seconds

# Where the English speaker starts each section: the end of the pause before it, measured from the
# audio's silences (fixture "vo_speech_starts"), not from Whisper's word times.
ENGLISH_STARTS = {
    "Kitab Mazmur, Alkitab Versi Borneo": 0.545,
    "Mazmur 1": 3.809,
    "Verse 1": 5.914,
    "Verse 2": 15.497,
    "Verse 3": 22.447,
    "Verse 4": 35.926,
    "Verse 5": 41.866,
    "Verse 6": 49.118,
}


@pytest.fixture(scope="module")
def demo():
    with open(os.path.join(HERE, "fixtures", "demo_ps1.json"), encoding="utf-8") as f:
        r = json.load(f)
    pairs = cli.read_anchors(os.path.join(HERE, "..", "demo", "DEMO_PS1.anchors.txt"))
    return r, cli.plan(pairs, r)


def test_every_anchor_becomes_its_own_section(demo):
    _, segs = demo
    assert len(segs) == len(ENGLISH_STARTS)


@pytest.mark.parametrize("name", ENGLISH_STARTS)
def test_a_section_starts_where_the_english_does(demo, name):
    _, segs = demo
    want = ENGLISH_STARTS[name]
    nearest = min((s[2] for s in segs), key=lambda t: abs(t - want))
    assert nearest == pytest.approx(want, abs=TOLERANCE), \
        f"{name}: the English starts at {want:.2f} s, but the nearest section starts at {nearest:.2f} s"


def test_english_starts_are_real_speech_starts(demo):
    # Guards the table above: every expected start is one the audio measurement found.
    r, _ = demo
    for t in ENGLISH_STARTS.values():
        assert min(abs(t - s) for s in r["vo_speech_starts"]) < 0.01


def test_malay_cuts_fall_on_pauses(demo):
    # Each Malay section is cut out of the ElevenLabs read; a cut that isn't at a pause clips a word.
    r, segs = demo
    pause_ends = [e for _, e in r["bp"]]
    for bm_start, *_ in segs:
        assert min(abs(bm_start - e) for e in pause_ends) < 0.01, f"cut at {bm_start:.2f} s is not at a pause"


def test_no_section_needs_more_than_the_tempo_limits(demo):
    _, segs = demo
    for bs, be, vs, ve in segs:
        assert (be - bs) / (ve - vs) <= cli.MAX_T
