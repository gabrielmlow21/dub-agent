"""Dub a voice-over into another language: time a TTS read to the source VO and mix it with the BGM.

Needs ffmpeg, plus whisper-cpp and a ggml model for `analyze` and `align`
(brew install ffmpeg whisper-cpp; model from
https://huggingface.co/ggerganov/whisper.cpp, e.g. ggml-small.bin).

Settings come from a profile folder (--profile DIR, or DUB_PROFILE), whose
profile.toml names the languages, the voice and the output label; see
profiles/template/. The profile's .env (then the current folder's) holds
ELEVENLABS_API_KEY. Command-line options win over the environment, which
wins over the profile.

  dub tts <text.txt> <out.mp3> [--voice ID] [--model ID] [--speed 1.0]
      Read a TTS text with ElevenLabs. Paragraphs are separated by blank
      lines; long texts are sent in chunks of whole paragraphs and joined.

  dub analyze <audio> --lang <whisper language> [--model ggml-small.bin]
      Print pauses and a timestamped transcript, to find section boundaries.

  dub align <name> <dub_audio> <vo_audio> <anchors.txt> [--bgm BGM] [--out DIR]
            [--source-lang en] [--target-lang ms]
      Find matching paragraph starts in both reads and write <out>/jobs.json.
      anchors.txt has one line per paragraph start, in order:
          Diberkatilah orang yang | Blessed is the man
      (the first few words of the dub's paragraph | of the source paragraph).
      Starts are found in Whisper word timings, snapped to the pause before
      them, and sections that would need speeding up past max_tempo are merged.
      A long silence at the end of a source section (a song, a clip) is
      kept silent in the dub.

  dub build <jobs.json>
      Build <out>/<name>.<label>.dub.wav and <name>.<label>.mix.wav for each job.

jobs.json:
  {"PS1": {"bm": ".../PS1.BM.mp3", "vo": ".../PS1.VO.wav",
           "bgm": ".../PS1.BGM.wav", "out": ".../PS1",   # bgm optional
           "label": "BM", "segs": [[bm_start, bm_end, vo_start, vo_end], ...]}}

Each seg pairs one section of the dub ("bm") with its source section ("vo"),
in seconds. Pick boundaries inside pauses. A seg that needs tempo > 1 usually
means a misplaced boundary, so check word timings there (analyze with -ml 1).
"""
import argparse, difflib, json, os, re, subprocess, sys, tempfile, urllib.error, urllib.request

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

MIN_T, MAX_T = 0.85, 1.25   # most a section is slowed / sped up
PAD = 0.05                  # padding around each section, seconds
SR = 48000
COMP = "acompressor=threshold=-24dB:ratio=3:attack=5:release=120"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{}?output_format=mp3_44100_192"
TTS_CHUNK = 4000            # characters per request, whole paragraphs
SNAP = 1.2                  # how far a paragraph start may move to reach a pause
LATE = 0.3                  # how far after a pause Whisper may place a word's start
SONG_GAP = 3.0              # a source silence this long is kept silent in the dub

# profile.toml keys and their defaults
PROFILE = {"source_lang": None, "target_lang": None, "label": None, "voice": None,
           "tts_model": "eleven_multilingual_v2", "whisper_model": "ggml-small.bin",
           "min_tempo": MIN_T, "max_tempo": MAX_T, "song_gap": SONG_GAP}


def ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args, check=True)


def dur(f):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]))


def lufs(args):
    err = subprocess.run(["ffmpeg", "-hide_banner"] + args + ["-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float([l for l in err.splitlines() if l.strip().startswith("I:")][-1].split()[1])


def silence(path, seconds):
    ff(["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{max(0, seconds):.4f}",
        "-c:a", "pcm_s16le", path])


def load_env(folders):
    """Read KEY=value lines from each folder's .env; the real environment wins."""
    for folder in folders:
        path = os.path.join(folder, ".env")
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#") and v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def load_profile(folder):
    """The profile's settings over the defaults; the tempo and gap limits are set globally."""
    global MIN_T, MAX_T, SONG_GAP
    prof = dict(PROFILE)
    if folder:
        path = os.path.join(folder, "profile.toml")
        if not os.path.exists(path):
            raise SystemExit(f"no profile.toml in {folder}")
        with open(path, "rb") as f:
            data = tomllib.load(f)
        unknown = set(data) - set(PROFILE)
        if unknown:
            raise SystemExit(f"{path}: unknown keys {', '.join(sorted(unknown))}")
        prof.update(data)
    prof["label"] = prof["label"] or (prof["target_lang"] or "dub").upper()
    MIN_T, MAX_T, SONG_GAP = prof["min_tempo"], prof["max_tempo"], prof["song_gap"]
    return prof


def chunks(text):
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    out = []
    for p in paras:
        if out and len(out[-1]) + len(p) + 2 <= TTS_CHUNK:
            out[-1] += "\n\n" + p
        else:
            out.append(p)
    return out


def tts(text_file, out, voice, model, speed):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key or not voice:
        raise SystemExit("set ELEVENLABS_API_KEY, and a voice in profile.toml, ELEVENLABS_VOICE_ID or --voice")
    parts = chunks(open(text_file, encoding="utf-8").read())
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, part in enumerate(parts):
            # Without voice_settings ElevenLabs uses the voice's own saved settings.
            body = {"text": part, "model_id": model}
            if speed != 1.0:
                body["voice_settings"] = {"speed": speed}
            # Neighbouring text keeps the intonation continuous across chunks.
            if i > 0:
                body["previous_text"] = parts[i - 1][-500:]
            if i < len(parts) - 1:
                body["next_text"] = parts[i + 1][:500]
            req = urllib.request.Request(TTS_URL.format(voice), json.dumps(body).encode(),
                                         {"xi-api-key": key, "Content-Type": "application/json"})
            try:
                audio = urllib.request.urlopen(req, timeout=300).read()
            except urllib.error.HTTPError as e:
                raise SystemExit(f"ElevenLabs error {e.code}: {e.read().decode(errors='replace')}")
            files.append(os.path.join(tmp, f"part{i:02d}.mp3"))
            open(files[-1], "wb").write(audio)
            print(f"  chunk {i + 1}/{len(parts)}: {len(part)} chars", flush=True)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in files)
        ff(["-f", "concat", "-safe", "0", "-i", lst, "-c:a", "libmp3lame", "-b:a", "192k", out])
    print(f"{out}: {dur(out):.2f}s")


def analyze(audio, lang, model):
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        ff(["-i", audio, "-ar", "16000", "-ac", "1", wav])
        err = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af",
                              "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        starts = [float(l.split("silence_start: ")[1]) for l in err.splitlines() if "silence_start" in l]
        ends = [float(l.split("silence_end: ")[1].split()[0]) for l in err.splitlines() if "silence_end" in l]
        print(f"duration {dur(audio):.2f}s\npauses: " +
              "  ".join(f"{s:.2f}-{e:.2f}" for s, e in zip(starts, ends)), flush=True)
        subprocess.run(["whisper-cli", "-m", model, "-l", lang, "-f", wav, "-np", "-ml", "70"],
                       stderr=subprocess.DEVNULL)


def words(audio, lang, model, tmp):
    """Whisper word timings as (start, end, normalised word)."""
    wav = os.path.join(tmp, lang + ".wav")
    ff(["-i", audio, "-ar", "16000", "-ac", "1", wav])
    out = subprocess.run(["whisper-cli", "-m", model, "-l", lang, "-f", wav, "-np", "-ml", "1", "-sow", "-mc", "0"],
                         capture_output=True, text=True).stdout
    w = []
    for l in out.splitlines():
        m = re.match(r"\[(\d+):(\d+):([\d.]+) --> (\d+):(\d+):([\d.]+)\]\s*(.*)", l)
        n = m and norm(m.group(7))
        if n:
            g = [float(x) for x in m.groups()[:6]]
            w.append((g[0] * 3600 + g[1] * 60 + g[2], g[3] * 3600 + g[4] * 60 + g[5], n[0]))
    return w, wav


def norm(text):
    return [x for x in (re.sub(r"[^\w'-]", "", t.lower()) for t in text.split()) if x]


def pauses(wav, d):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af", f"silencedetect=noise=-35dB:d={d}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    st = [float(l.split("silence_start: ")[1]) for l in err.splitlines() if "silence_start" in l]
    en = [float(l.split("silence_end: ")[1].split()[0]) for l in err.splitlines() if "silence_end" in l]
    return list(zip(st, en))


def snap(t, ps):
    """Move a paragraph start found by Whisper (t) to where the speech after a pause begins.
    Whisper tends to put a word's start inside the silence before it, so the right pause usually
    ends just after t: take the first pause that ends from LATE seconds before t up to SNAP after.
    (The nearest pause could be a breath before t, which starts the section early.)"""
    ends = [e for s, e in ps if t - LATE <= e <= t + SNAP]
    return min(ends) if ends else t


def find(w, phrase, cur, window=500):
    """Index of the word where phrase starts, searching forward from cur, and the match score.
    Compares letters rather than words, so differences in how Whisper splits words don't matter."""
    cur = min(cur, len(w) - 1)
    target, best = "".join(phrase), (0.0, cur)
    for i in range(cur, min(len(w), cur + window)):
        cand = ""
        for j in range(i, min(len(w), i + len(phrase) + 3)):
            cand += w[j][2]
            if len(cand) >= len(target):
                break
        r = difflib.SequenceMatcher(None, target, cand[:len(target) + 3]).ratio()
        if r > best[0]:
            best = (r, i)
        # A start one word early can already score well ("1 di berkatila..." for "diberkatilah"),
        # so look two words past a good match before settling on it.
        if best[0] > 0.9 and i >= best[1] + 2:
            break
    return best[1], best[0]


def listen(bm, vo, model, bm_lang, vo_lang):
    """What align needs to know about both reads: Whisper word timings, pauses and lengths.
    This is the slow part (Whisper and ffmpeg); plan() works from its result alone."""
    with tempfile.TemporaryDirectory() as tmp:
        print(f"transcribing the dub read ({bm_lang})...", flush=True)
        bw, bwav = words(bm, bm_lang, model, tmp)
        print(f"transcribing the source VO ({vo_lang})...", flush=True)
        vw, vwav = words(vo, vo_lang, model, tmp)
        return {"bw": bw, "vw": vw, "bp": pauses(bwav, 0.12), "vp": pauses(vwav, 0.12),
                "gaps": [(s, e) for s, e in pauses(vwav, SONG_GAP) if s > 1],
                "bend": dur(bm), "vend": dur(vo)}


def plan(pairs, r):
    """Sections [bm_start, bm_end, vo_start, vo_end] from anchor pairs and listen()'s result."""
    bw, vw, bp, vp = r["bw"], r["vw"], r["bp"], r["vp"]
    # Speech starts: the end of the leading silence in each read.
    pts = [(bp[0][1] if bp and bp[0][0] < 0.05 else 0.0, vp[0][1] if vp and vp[0][0] < 0.05 else 0.0, "start")]
    cb = cv = 0
    for b, v in pairs:
        ib, rb = find(bw, norm(b), cb)
        iv, rv = find(vw, norm(v), cv)
        flag = "" if min(rb, rv) > 0.75 else "   <-- check this one"
        print(f"  {bw[ib][0]:8.2f} {vw[iv][0]:8.2f}  {rb:.2f} {rv:.2f}  {b[:28]:28s} | {v[:28]}{flag}")
        cb, cv = ib + 1, iv + 1
        sb, sv = snap(bw[ib][0], bp), snap(vw[iv][0], vp)
        # Two starts snapped to the same pause would make an empty section.
        if sb <= pts[-1][0] or sv <= pts[-1][1]:
            print(f"  merged {b[:40]!r} into the section before it (same start)")
            continue
        pts.append((sb, sv, b))
    bend, vend = r["bend"], r["vend"]

    def sections():
        return [[b, pts[i + 1][0] if i + 1 < len(pts) else bend, v, pts[i + 1][1] if i + 1 < len(pts) else vend, n]
                for i, (b, v, n) in enumerate(pts)]
    while True:
        bad = [i for i, (b, b2, v, v2, n) in enumerate(sections()) if v2 > v and (b2 - b) / (v2 - v) > MAX_T]
        if not bad or bad[0] + 1 >= len(pts):
            break
        print(f"  merged {pts[bad[0] + 1][2][:40]!r} into the section before it (too fast otherwise)")
        del pts[bad[0] + 1]
    segs = sections()
    for s, e in r["gaps"]:
        for sec in segs:
            if sec[2] < s < sec[3]:
                if sec[3] - e <= SNAP:
                    sec[3] = s    # end the section where the source goes silent
                else:
                    print(f"  WARNING a {e - s:.0f}s silence at {s:.1f}s is inside section {sec[4][:30]!r}: "
                          "add an anchor right after it")
    # Leave a small gap so the padding around back-to-back sections doesn't push them late.
    for a, b in zip(segs, segs[1:]):
        a[1], a[3] = min(a[1], b[0] - 2 * PAD), min(a[3], b[2] - 2 * PAD)
    # The last section has nothing after it to merge with, and a silence can shorten any section:
    # build caps those at max_tempo, so they run long.
    for b, b2, v, v2, n in segs:
        if v2 > v and (b2 - b) / (v2 - v) > MAX_T:
            print(f"  WARNING section {n[:30]!r} needs speeding up {(b2 - b) / (v2 - v):.2f}x, past max_tempo "
                  f"{MAX_T}: build will cap it and it will run {(b2 - b) / MAX_T - (v2 - v):.1f}s long")
    return [[round(x, 3) for x in sec[:4]] for sec in segs]


def read_anchors(path):
    return [[x.strip() for x in l.split("|")] for l in open(path, encoding="utf-8") if "|" in l]


def align(name, bm, vo, anchors, bgm, out, model, vo_lang, bm_lang, label):
    segs = plan(read_anchors(anchors), listen(bm, vo, model, bm_lang, vo_lang))
    job = {"bm": bm, "vo": vo, "out": out, "label": label, "segs": segs}
    if bgm:
        job["bgm"] = bgm
    path = os.path.join(out, "jobs.json")
    jobs = json.load(open(path)) if os.path.exists(path) else {}
    jobs[name] = job
    json.dump(jobs, open(path, "w"), indent=1)
    print(f"{len(segs)} sections written to {path}")


def build(name, job):
    total = dur(job["vo"])
    out = job.get("out", os.path.dirname(job["vo"]))
    bgm = f"BGM {dur(job['bgm']):.2f}s" if job.get("bgm") else "no BGM"
    label = job.get("label", "BM")
    print(f"=== {name}: VO {total:.2f}s, {bgm}, {label} {dur(job['bm']):.2f}s")
    with tempfile.TemporaryDirectory() as tmp:
        files, cursor = [], 0.0
        for i, (bs, be, ts, te) in enumerate(job["segs"]):
            bs, be, ts, te = max(0, bs - PAD), be + PAD, max(0, ts - PAD), te + PAD
            t = min(max((be - bs) / (te - ts), MIN_T), MAX_T)
            seg = os.path.join(tmp, f"seg{i:02d}.wav")
            ff(["-i", job["bm"], "-af",
                f"atrim={bs}:{be},asetpts=PTS-STARTPTS,aresample={SR},atempo={t:.5f},"
                f"afade=t=in:d=0.02,afade=t=out:st={(be - bs) / t - 0.03:.3f}:d=0.03",
                "-ac", "1", "-c:a", "pcm_s16le", seg])
            if ts < cursor:
                print(f"  WARNING section {i + 1} overlaps the previous one by {cursor - ts:.2f}s")
            # adelay doesn't place segments reliably in ffmpeg 9, so join with silence files
            gap = os.path.join(tmp, f"gap{i:02d}.wav")
            silence(gap, ts - cursor)
            files += [gap, seg]
            cursor = max(cursor, ts) + dur(seg)
            early = (te - ts) - dur(seg)
            print(f"  {i + 1:2d} start {ts + PAD:6.2f} tempo {t:.3f}" +
                  (f"  ends {early:.1f}s early" if early > 0.8 else "") +
                  (f"  WARNING capped at max_tempo, runs {-early:.1f}s long" if early < -0.05 else ""))
        tail = os.path.join(tmp, "tail.wav")
        silence(tail, total - cursor)
        files.append(tail)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in files)
        dub = os.path.join(out, f"{name}.{label}.dub.wav")
        ff(["-f", "concat", "-safe", "0", "-i", lst, "-af", "pan=stereo|c0=c0|c1=c0",
            "-ar", str(SR), "-c:a", "pcm_s16le", dub])

    # Bring the dub's voice to the source VO's loudness; leave the BGM as delivered.
    gain = lufs(["-i", job["vo"], "-af", "ebur128=framelog=quiet"]) - \
        lufs(["-i", dub, "-af", COMP + ",ebur128=framelog=quiet"])
    if not job.get("bgm"):
        # No music bed: level-match the dub itself.
        raw = dub + ".raw.wav"
        os.replace(dub, raw)
        ff(["-i", raw, "-af", f"{COMP},volume={gain:.2f}dB,alimiter=limit=0.89:level=false",
            "-ar", str(SR), "-c:a", "pcm_s16le", dub])
        os.remove(raw)
        print(f"  dub {dur(dub):.2f}s, {lufs(['-i', dub, '-af', 'ebur128=framelog=quiet']):.1f} LUFS "
              f"(source VO {lufs(['-i', job['vo'], '-af', 'ebur128=framelog=quiet']):.1f})")
        return
    mix = os.path.join(out, f"{name}.{label}.mix.wav")
    ff(["-i", dub, "-i", job["bgm"], "-filter_complex",
        f"[0:a]{COMP},volume={gain:.2f}dB,alimiter=limit=0.89:level=false[v];"
        "[v][1:a]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.94:level=false[out]",
        "-map", "[out]", "-ar", str(SR), "-c:a", "pcm_s16le", mix])
    ref = lufs(["-i", job["vo"], "-i", job["bgm"], "-filter_complex",
                "amix=inputs=2:normalize=0,ebur128=framelog=quiet"])
    print(f"  mix {dur(mix):.2f}s, {lufs(['-i', mix, '-af', 'ebur128=framelog=quiet']):.1f} LUFS "
          f"(source mix {ref:.1f}), voice gain {gain:+.1f} dB")


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--profile", default=os.environ.get("DUB_PROFILE"),
                     help="profile folder with profile.toml (or set DUB_PROFILE)")
    p = argparse.ArgumentParser(prog="dub")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", parents=[pre])
    a.add_argument("audio")
    a.add_argument("--lang", required=True)
    a.add_argument("--model")
    al = sub.add_parser("align", parents=[pre])
    for x in ("name", "bm", "vo", "anchors"):
        al.add_argument(x)
    al.add_argument("--bgm")
    al.add_argument("--out")
    al.add_argument("--source-lang", help="language of the source VO, e.g. tl, en")
    al.add_argument("--target-lang", help="language of the dub, e.g. ms")
    al.add_argument("--model")
    b = sub.add_parser("build", parents=[pre])
    b.add_argument("jobs")
    t = sub.add_parser("tts", parents=[pre])
    t.add_argument("text")
    t.add_argument("out")
    t.add_argument("--voice")
    t.add_argument("--model")
    t.add_argument("--speed", type=float, default=1.0, help="0.7-1.2")
    args = p.parse_args()
    prof = load_profile(args.profile)
    load_env(([args.profile] if args.profile else []) + [os.getcwd()])
    env = os.environ.get
    if args.cmd == "align":
        src, tgt = args.source_lang or prof["source_lang"], args.target_lang or prof["target_lang"]
        if not (src and tgt):
            raise SystemExit("set source_lang and target_lang in profile.toml, or --source-lang and --target-lang")
        align(args.name, args.bm, args.vo, args.anchors, args.bgm,
              args.out or os.path.dirname(os.path.abspath(args.vo)),
              args.model or env("WHISPER_MODEL") or prof["whisper_model"],
              src, tgt, prof["label"])
    elif args.cmd == "tts":
        voice = args.voice or env("ELEVENLABS_VOICE_ID") or prof["voice"]
        tts(args.text, args.out, voice, args.model or env("ELEVENLABS_MODEL") or prof["tts_model"], args.speed)
    elif args.cmd == "analyze":
        analyze(args.audio, args.lang, args.model or env("WHISPER_MODEL") or prof["whisper_model"])
    else:
        for name, job in json.load(open(args.jobs)).items():
            build(name, job)


if __name__ == "__main__":
    main()
