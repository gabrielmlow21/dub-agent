# Instructions for Claude (<client>, <source> → <target>)

When translating:
- Follow `style-guide.md` and use the terms in `glossary.md`.
- If a key term isn't in the glossary, propose an entry rather than silently choosing one.

## "Dub it" in one go
When asked to dub a script, run the whole chain without stopping between steps. `<NAME>` comes from the source VO's file name (`<NAME>.VO.wav`); `<LABEL>` is the label in `profile.toml`.
1. Translate the script, if it isn't done yet.
2. Write the voice text from the translation (rules in `style-guide.md`), one paragraph per block with a blank line between, and save it as `<NAME>.<LABEL>.tts.txt`.
3. `dub tts --profile <this folder> <NAME>.<LABEL>.tts.txt <NAME>.<LABEL>.mp3`
4. Write `<NAME>.anchors.txt`: one line per paragraph start, with the first few words of the translated paragraph and of the source paragraph it pairs with (`translated words | source words`).
5. `dub align --profile <this folder> <NAME> <NAME>.<LABEL>.mp3 <NAME>.VO.wav <NAME>.anchors.txt [--bgm <NAME>.BGM.wav]`. Fix anchors flagged "check this one" and run it again. "merged … (same start)" or several "merged … (too fast)" lines in a row usually mean an anchor is wrong; check the word timings there with `dub analyze`.
6. `dub build --profile <this folder> jobs.json`
7. Report the sections that end well early (over ~1 s), so the reviewer can decide whether to re-read them slower with `dub tts --speed` (0.7–1.2).

Stop and ask only if an input file is missing or the TTS service returns an error.
