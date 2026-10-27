---
name: dub
description: Translate a script and dub it onto the original voice-over with the `dub` tool, timed section by section, using a client profile. Use when the user asks to dub, voice or re-voice a script or video into another language, or to re-time or re-mix an existing dub.
---

# Dub a script

Turn a script plus its original voice track into a translated voice-over that starts each section where the original speaker did, mixed over the original music.

## Before you start

1. **Find the profile.** The user names it, or it's in `DUB_PROFILE`, or the current folder has a `profile.toml`. If none of these, ask which profile to use. Don't guess.
2. **Read the whole profile:** `profile.toml` (languages, label, voice), its `CLAUDE.md` if there is one, and the glossary and style guide (`glossary.md` and `style-guide.md`, or wherever the profile's `CLAUDE.md` says they are). The profile's rules win over anything in this skill.
3. **Find the inputs.** In the audio folder: `<NAME>.VO.wav` (the original voice), optionally `<NAME>.BGM.wav` (the music bed), and the script. `<NAME>` comes from the VO's file name; `<LABEL>` is the profile's label. If the VO is missing, you can still translate and read the text, but stop before `align` and say there was nothing to time it to.
4. **Check the tools:** `dub --help`, `ffmpeg -version`, `whisper-cli --help`. If one is missing, say which and how to install it (README, "Install"), and stop.

## Steps

Run the whole chain without stopping between steps, unless a step below says to stop.

1. **Translate** the script into the profile's target language, if it isn't done yet. Follow the glossary and style guide. When a key term isn't in the glossary, use your best rendering and list it as a proposed glossary entry in your report. Don't add it to the glossary yourself.
2. **Write the voice text:** what the voice should actually say. Follow the style guide's voice-text rules (no headings or production cues; references, numbers and acronyms written the way they're spoken). One paragraph per block, with a blank line between. Save it as `<NAME>.<LABEL>.tts.txt` next to the audio.
3. **Read it:** `dub tts --profile <PROFILE> <NAME>.<LABEL>.tts.txt <NAME>.<LABEL>.mp3`
4. **Write the anchors:** `<NAME>.anchors.txt`, one line per paragraph start, in order: the first 3–5 words of the translated paragraph, then `|`, then the first 3–5 words of the original paragraph it pairs with.
   - Take the original's words from the script, not from memory.
   - Pick words that appear only once nearby. A word that's repeated close by (a title, a name) can match the wrong spot; add the next word or two to make it unique.
5. **Align:** `dub align --profile <PROFILE> <NAME> <NAME>.<LABEL>.mp3 <NAME>.VO.wav <NAME>.anchors.txt [--bgm <NAME>.BGM.wav]`. Read its output and fix problems before building (next section).
6. **Build:** `dub build --profile <PROFILE> jobs.json` (`jobs.json` is in the audio folder).
7. **Check the result** (below), then **report**.

## Reading `align`'s output

Each anchor prints a line with its time in both reads and two match scores (dub, original):

| You see | It usually means | Do this |
|---|---|---|
| `<-- check this one` (a score under 0.75) | The words weren't found as written: a typo, or Whisper heard them differently | Look at the transcript around that time with `dub analyze <audio> --lang <code>`, change the anchor to the words Whisper actually heard, and run `align` again |
| `merged … (same start)` | Two anchors snapped to the same pause; often a heading straight before its first paragraph | Fine if they really are back to back. Otherwise the second anchor matched too early; make it more specific |
| One `merged … (too fast otherwise)` | That section would need more than the profile's `max_tempo` speed-up | Fine on its own: it's joined to the section before |
| Several `merged … (too fast)` in a row | An anchor before them landed on the wrong spot | Find it: its time jumps compared to its neighbours. Fix it and run `align` again |
| `WARNING a Ns silence … add an anchor right after it` | A song or clip in the original falls in the middle of a section | Add an anchor for the paragraph that starts after the silence |

Re-run `align` until no anchor is flagged and any merges are explained. If the same anchor still fails after two attempts, leave it, build anyway, and say so in the report.

## Checking the result

- `build` prints each section's start and tempo. Note any section that `ends Ns early` (over ~1 s) and any `WARNING … overlaps`.
- Confirm the length and loudness line: the dub must be as long as the VO, and its loudness close to the VO's.
- Spot-check the timing: transcribe the dub with `dub analyze <NAME>.<LABEL>.dub.wav --lang <target>` and compare a few section starts with the original's. They should be within about half a second.

## Report

Keep it short, in this order:

1. **Outputs:** the paths of `<NAME>.<LABEL>.dub.wav` and, with a BGM, `<NAME>.<LABEL>.mix.wav`.
2. **Timing:** how many sections, any that end more than ~1 s early (the user may want those re-read slower with `dub tts --speed 0.7`–`1.2`), and any overlaps.
3. **Anything you fixed or couldn't fix:** anchors you changed and why, warnings left.
4. **Proposed glossary entries,** if any, for the user to approve.

## Stop and ask only if

- the profile or the audio folder can't be found,
- a tool is missing,
- ElevenLabs returns an error (quote it; don't retry more than once),
- or the profile's rules contradict each other.

Never commit, push or upload anything as part of a dub. The outputs stay next to the inputs for the user to check.
