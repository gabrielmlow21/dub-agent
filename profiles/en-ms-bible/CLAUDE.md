# Instructions for Claude (English → Malaysian Malay, Bible content)

This profile was used for the Psalm 1 demo. A real client's profile has the same files with its own rules, and stays private.

When translating:
- Follow `style-guide.md` and use the terms in `glossary.md`.
- Use Malaysian Malay, never Indonesian.
- If a key term isn't in the glossary, propose an entry rather than silently choosing one.

## "Dub it" in one go
When asked to dub a script, run the whole chain without stopping between steps. Audio for each script lives in its own folder; `<NAME>` comes from the source VO's file name (`<NAME>.VO.wav`).
1. Translate the script, if it isn't done yet.
2. Write the voice text from the translation (rules in `style-guide.md`), one paragraph or verse per block with a blank line between, and save it as `<NAME>.BM.tts.txt`.
3. `dub tts --profile <this folder> <NAME>.BM.tts.txt <NAME>.BM.mp3`
4. Write `<NAME>.anchors.txt`: one line per paragraph start, with the first few words of the Malay paragraph and of the source paragraph it pairs with (`Diberkatilah orang yang | Blessed is the man`).
5. `dub align --profile <this folder> <NAME> <NAME>.BM.mp3 <NAME>.VO.wav <NAME>.anchors.txt [--bgm <NAME>.BGM.wav]`. It flags anchors with a low match score ("check this one"): fix those words and run it again. "merged … (same start)" or several "merged … (too fast)" lines in a row usually mean an anchor is wrong; check the word timings there with `dub analyze`.
6. `dub build --profile <this folder> jobs.json`
7. Report the sections that end well early (over ~1 s), so the reviewer can decide whether to re-read them slower with `dub tts --speed` (0.7–1.2).

Stop and ask only if an input file is missing or ElevenLabs returns an error.
