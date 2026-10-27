"""Record what align hears in the Psalm 1 demo, for the timing test.

Needs the demo's audio, ffmpeg and Whisper, so it isn't run by the tests; run it again only when
the demo audio or Whisper model changes:

    uv run python tests/record_demo.py <audio folder> <ggml model>

The audio folder holds DEMO_PS1.BM.mp3 (the ElevenLabs read) and DEMO_PS1.VO.wav (the LibriVox
clip). Writes tests/fixtures/demo_ps1.json: listen()'s result, plus where speech really starts
after each pause in both reads, measured from the audio's silences rather than from Whisper.
"""
import json, os, sys

from dub import cli

SPEECH_GAP = 0.35   # a silence this long before speech counts as a pause


def speech_starts(path):
    end = cli.dur(path)
    return [round(e, 3) for s, e in cli.pauses(path, SPEECH_GAP) if e < end - 0.2]


def main(folder, model):
    bm, vo = os.path.join(folder, "DEMO_PS1.BM.mp3"), os.path.join(folder, "DEMO_PS1.VO.wav")
    r = cli.listen(bm, vo, model, "ms", "en")
    r["bm_speech_starts"], r["vo_speech_starts"] = speech_starts(bm), speech_starts(vo)
    out = os.path.join(os.path.dirname(__file__), "fixtures", "demo_ps1.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=0)
    print(f"{out}: {len(r['bw'])} + {len(r['vw'])} words")


if __name__ == "__main__":
    main(*sys.argv[1:3])
