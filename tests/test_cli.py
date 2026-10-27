"""Tests for the parts of dub that don't need audio, ffmpeg or Whisper."""
import os

import pytest

from dub import cli


def words(*tokens, step=0.5):
    """Whisper-style word timings: (start, end, normalised word), half a second apart."""
    return [(i * step, i * step + step, t) for i, t in enumerate(tokens)]


# norm: how anchor phrases and transcript words are compared

def test_norm_lowercases_and_drops_punctuation():
    assert cli.norm("Tetapi dia BERSUKACITA, dengan hukum!") == ["tetapi", "dia", "bersukacita", "dengan", "hukum"]


def test_norm_keeps_hyphens_and_apostrophes():
    assert cli.norm("hukum-Nya don't") == ["hukum-nya", "don't"]


def test_norm_drops_tokens_that_are_only_punctuation():
    assert cli.norm("— , Mazmur 1 .") == ["mazmur", "1"]


# chunks: how a long TTS text is split into ElevenLabs requests

def test_chunks_joins_short_paragraphs_into_one_request():
    assert cli.chunks("One.\n\nTwo.\n\nThree.") == ["One.\n\nTwo.\n\nThree."]


def test_chunks_starts_a_new_request_when_the_next_paragraph_would_not_fit(monkeypatch):
    monkeypatch.setattr(cli, "TTS_CHUNK", 10)
    assert cli.chunks("aaaa\n\nbbbb\n\ncccc") == ["aaaa\n\nbbbb", "cccc"]


def test_chunks_never_splits_a_paragraph(monkeypatch):
    monkeypatch.setattr(cli, "TTS_CHUNK", 10)
    long = "a very long paragraph that is over the limit"
    assert cli.chunks(f"short\n\n{long}") == ["short", long]


def test_chunks_ignores_extra_blank_lines():
    assert cli.chunks("\n\nOne.\n\n\n\nTwo.\n\n") == ["One.\n\nTwo."]


# find: locating an anchor phrase in a transcript

def test_find_locates_an_exact_phrase():
    w = words("kitab", "mazmur", "tetapi", "dia", "bersukacita")
    assert cli.find(w, cli.norm("Tetapi dia bersukacita"), 0) == (2, 1.0)


def test_find_matches_when_whisper_splits_words_differently():
    # Whisper heard "Diberkatilah" as "di berkatila", one letter short.
    w = words("mazmur", "1", "di", "berkatila", "orang", "yang")
    i, score = cli.find(w, cli.norm("Diberkatilah orang yang"), 0)
    assert i == 2
    assert score > 0.75


def test_find_searches_forward_from_the_previous_anchor():
    # A phrase that comes twice: each search starts after the previous anchor, so it finds the next one.
    w = words("mazmur", "1", "diberkatilah", "mazmur", "2", "mengapa")
    assert cli.find(w, cli.norm("Mazmur"), 0) == (0, 1.0)
    assert cli.find(w, cli.norm("Mazmur"), 1) == (3, 1.0)


def test_find_scores_a_missing_phrase_low():
    w = words("kerana", "tuan", "menaungi", "jalan")
    assert cli.find(w, cli.norm("orang zalim akan binasa"), 0)[1] < 0.75


def test_find_does_not_crash_when_starting_past_the_end():
    # Fixed bug: an anchor after the last word used to crash align.
    w = words("akhir", "sekali")
    i, _ = cli.find(w, cli.norm("tiada lagi"), 10)
    assert i == len(w) - 1


# snap: moving a start found by Whisper to where the speech after a pause begins

def test_snap_skips_a_breath_just_before_the_word():
    # Demo verse 5: Whisper put "Therefore" at 41.00 s. A breath ends at 40.20 s (closer), but the
    # pause before "Therefore" ends at 41.87 s. Taking the breath started the Malay 1.6 s early.
    ps = [(39.9, 40.20), (40.53, 41.87)]
    assert cli.snap(41.00, ps) == 41.87


def test_snap_uses_the_end_of_the_silence_whisper_put_the_word_in():
    assert cli.snap(5.24, [(3.0, 3.81), (5.0, 5.91)]) == 5.91


def test_snap_allows_whisper_to_be_slightly_late():
    assert cli.snap(15.70, [(14.8, 15.50)]) == 15.50


def test_snap_takes_the_first_pause_when_there_are_several():
    assert cli.snap(10.0, [(10.1, 10.4), (10.8, 11.0)]) == 10.4


def test_snap_leaves_the_start_alone_with_no_pause_in_reach():
    assert cli.snap(20.0, [(10.0, 11.0), (25.0, 26.0)]) == 20.0


# load_profile: reading profile.toml

@pytest.fixture
def keep_limits(monkeypatch):
    """load_profile sets the timing limits for the whole module; put them back after each test."""
    for name in ("MIN_T", "MAX_T", "SONG_GAP"):
        monkeypatch.setattr(cli, name, getattr(cli, name))


def write_profile(folder, text):
    (folder / "profile.toml").write_text(text, encoding="utf-8")
    return str(folder)


def test_profile_values_override_the_defaults(tmp_path, keep_limits):
    p = cli.load_profile(write_profile(tmp_path, 'source_lang = "en"\ntarget_lang = "ms"\nlabel = "BM"\nvoice = "abc"\n'))
    assert (p["source_lang"], p["target_lang"], p["label"], p["voice"]) == ("en", "ms", "BM", "abc")
    assert p["tts_model"] == "eleven_multilingual_v2"


def test_label_defaults_to_the_target_language_in_capitals(tmp_path, keep_limits):
    p = cli.load_profile(write_profile(tmp_path, 'source_lang = "en"\ntarget_lang = "fr"\n'))
    assert p["label"] == "FR"


def test_profile_sets_the_timing_limits(tmp_path, keep_limits):
    cli.load_profile(write_profile(tmp_path, 'max_tempo = 1.1\nmin_tempo = 0.9\nsong_gap = 5.0\n'))
    assert (cli.MIN_T, cli.MAX_T, cli.SONG_GAP) == (0.9, 1.1, 5.0)


def test_unknown_profile_keys_are_rejected(tmp_path, keep_limits):
    # Catches typos such as "voise" instead of silently ignoring them.
    with pytest.raises(SystemExit, match="unknown keys voise"):
        cli.load_profile(write_profile(tmp_path, 'voise = "abc"\n'))


def test_a_folder_without_profile_toml_is_rejected(tmp_path, keep_limits):
    with pytest.raises(SystemExit, match="no profile.toml"):
        cli.load_profile(str(tmp_path))


def test_no_profile_gives_the_defaults(keep_limits):
    p = cli.load_profile(None)
    assert p["source_lang"] is None and p["target_lang"] is None
    assert p["label"] == "DUB"


def test_the_shipped_profiles_load(keep_limits):
    root = os.path.join(os.path.dirname(__file__), "..", "profiles")
    assert cli.load_profile(os.path.join(root, "en-ms-bible"))["target_lang"] == "ms"
    assert cli.load_profile(os.path.join(root, "template"))["source_lang"] == ""


# load_env: reading .env files

def test_env_files_fill_in_missing_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("DUB_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text('# a comment\nDUB_TEST_KEY="from file"\n', encoding="utf-8")
    cli.load_env([str(tmp_path)])
    assert os.environ["DUB_TEST_KEY"] == "from file"


def test_the_real_environment_wins_over_env_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DUB_TEST_KEY", "from shell")
    (tmp_path / ".env").write_text("DUB_TEST_KEY=from file\n", encoding="utf-8")
    cli.load_env([str(tmp_path)])
    assert os.environ["DUB_TEST_KEY"] == "from shell"


def test_the_first_env_file_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("DUB_TEST_KEY", raising=False)
    profile, cwd = tmp_path / "profile", tmp_path / "cwd"
    profile.mkdir()
    cwd.mkdir()
    (profile / ".env").write_text("DUB_TEST_KEY=profile\n", encoding="utf-8")
    (cwd / ".env").write_text("DUB_TEST_KEY=cwd\n", encoding="utf-8")
    cli.load_env([str(profile), str(cwd)])
    assert os.environ["DUB_TEST_KEY"] == "profile"
