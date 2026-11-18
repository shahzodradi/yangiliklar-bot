"""Yangilikdan 1080x1920 Instagram REEL: rasm sekin yaqinlashadi (Ken Burns), pastda R3 uslubidagi sarlavha paneli,
o'zbekcha ovoz (edge-tts, bepul: uz-UZ-SardorNeural) sarlavha + mazmunni o'qiydi, pastda progress chiziq.
Ishlatish: make_reel(nom, rasm_yo'li|None, sarlavha_html, o'qiladigan_matn) -> mp4 yo'li yoki None."""
import asyncio, json, pathlib, platform, re, shutil, subprocess, uuid

HERE = pathlib.Path(__file__).parent
OUT = HERE / "chiqish"
VOICE = "uz-UZ-SardorNeural"
W, H, PH = 1080, 1920, 1100   # rasm maydoni balandligi (qolgani qora panel)

PANEL = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@800&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden;background:transparent}
.fade{position:absolute;top:%(fade)spx;height:160px;width:100%%;background:linear-gradient(transparent,#000)}
.p{position:absolute;top:%(ph)spx;height:%(ph2)spx;width:100%%;background:#000;display:flex;flex-direction:column;
 align-items:center;padding:0 50px}
.sm{font-family:Inter,sans-serif;font-weight:800;letter-spacing:2px;text-transform:uppercase}
.logo{font-size:26px;color:#fff;margin-top:-22px;background:#000;padding:6px 20px}
h1{font-family:Anton,Impact,sans-serif;font-weight:400;text-transform:uppercase;font-size:%(fs)spx;line-height:1.08;
 color:#fff;text-align:center;margin-top:44px}
h1 b{color:#3d8bff;font-weight:400;white-space:nowrap}
.sw{font-size:28px;color:#cfcfcf;margin-top:auto;margin-bottom:150px}
</style></head><body><div class="fade"></div><div class="p"><div class="logo sm">AI.TezkorYangiliklari</div>
<h1>%(h1)s</h1><div class="sw sm">Batafsil izohlarda oʻqing 👇</div></div></body></html>"""


def esc(path):
    """ffmpeg filtr ichidagi yo'l: C:/... -> C\\:/..."""
    return str(pathlib.Path(path).resolve()).replace("\\", "/").replace(":", r"\:")


def ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def browser_shot(html_path, png, w, h, transparent=False):
    flags = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
             "--allow-file-access-from-files", "--virtual-time-budget=6000", f"--window-size={w},{h}",
             f"--screenshot={png}", html_path.resolve().as_uri()]
    if transparent:
        flags.insert(0, "--default-background-color=00000000")
    profile = OUT / f"_edge_{uuid.uuid4().hex[:6]}"
    flags.insert(0, f"--user-data-dir={profile}")
    if platform.system() == "Windows":
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        args = ",".join(f"'{a}'" for a in flags)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process -FilePath '{edge}' -ArgumentList {args} -Wait"], timeout=120)
    else:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or "chromium-browser"
        subprocess.run([chrome, *flags], timeout=120, capture_output=True)
    shutil.rmtree(profile, ignore_errors=True)
    return png.exists()


CARD = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1100px;overflow:hidden;background:radial-gradient(circle at %(gx)s%% %(gy)s%%,%(glow)s,#020814 72%%)}
.n{position:absolute;left:70px;top:150px;font-family:Anton,Impact,sans-serif;font-size:420px;line-height:1;
 color:transparent;-webkit-text-stroke:6px #3d8bff;opacity:.9}
.t{position:absolute;left:80px;right:80px;top:590px;font-family:Anton,Impact,sans-serif;font-size:%(fs)spx;
 line-height:1.08;color:#fff;text-transform:uppercase}
</style></head><body><div class="n">%(num)s</div><div class="t">%(text)s</div></body></html>"""


def title_card(name, num, text):
    """Yangi rasm topilmagan voqea uchun rasm o'rniga: katta raqam + sarlavha (logo takrorlanmasin)."""
    import html, random
    OUT.mkdir(exist_ok=True)
    page, png = OUT / f"{name}.html", OUT / f"{name}.png"
    fs = 96 if len(text) <= 40 else 80 if len(text) <= 65 else 68
    page.write_text(CARD % dict(num=num, text=html.escape(text), fs=fs,
                                glow=random.choice(["#0d3b8f", "#5b1fa8", "#0f6e6e", "#8f1d3b", "#1f4fd1", "#2b2b8f"]),
                                gx=random.randint(55, 85), gy=random.randint(15, 45)), encoding="utf-8")
    return png if browser_shot(page, png, W, PH) else None


def voice(text, mp3):
    """MP3 yozadi va so'zlar vaqtini qaytaradi: [(boshlanish_s, tugash_s, so'z)].
    Asosiy: Gemini TTS (Charon — tabiiy o'zbekcha); limit/xato bo'lsa edge-tts Sardor."""
    try:
        return gemini_voice(text, mp3)
    except Exception as e:
        print("Gemini ovoz ishlamadi, Sardor bilan:", str(e)[:200])
        return edge_voice(text, mp3)


GEMINI_VOICE = "Charon"


def gemini_voice(text, mp3):
    """Gemini so'z vaqtini bermaydi: jimlik joylarini (ffmpeg silencedetect) gap chegaralariga moslab,
    har bo'lak ichida so'zlarni harf soniga qarab taqsimlaymiz — subtitr ovozga yaqin tushadi."""
    import base64, os, urllib.request
    key = os.environ.get("GEMINI_API_KEY") or next(l.split("=", 1)[1].strip()
          for l in (HERE.parent / ".env").read_text().splitlines() if l.startswith("GEMINI_API_KEY="))
    body = {"contents": [{"parts": [{"text": "Read this Uzbek news aloud in clear, natural native Uzbek "
                                     "(Tashkent pronunciation), energetic news-anchor tone: " + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}}
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/"
                                 "gemini-2.5-flash-preview-tts:generateContent", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "x-goog-api-key": key})
    d = json.load(urllib.request.urlopen(req, timeout=180))
    pcm = mp3.with_suffix(".pcm")
    pcm.write_bytes(base64.b64decode(d["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]))
    raw = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(pcm)]
    subprocess.run(raw + ["-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                          "silenceremove=start_periods=1:start_threshold=-45dB,areverse", "-b:a", "160k", str(mp3)], check=True)
    pcm.unlink()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3)],
                               capture_output=True, text=True).stdout)
    log = subprocess.run(["ffmpeg", "-i", str(mp3), "-af", "silencedetect=n=-38dB:d=0.15", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    pauses = [(a, b) for a, b in zip(st, en) if 0.05 < a and b < dur - 0.05]
    chunks = [c.split() for c in re.split(r"(?<=[.!?,;:—])\s+", text.strip()) if c.split()]
    k = min(len(chunks) - 1, len(pauses))  # eng uzun k ta jimlik = bo'lak chegaralari
    cuts = sorted(sorted(pauses, key=lambda p: p[0] - p[1])[:k])
    if k < len(chunks) - 1:  # jimlik kam: bo'laklarni birlashtiramiz
        flat = [w for c in chunks for w in c]
        chunks = [flat] if not k else chunks[:k] + [[w for c in chunks[k:] for w in c]]
    spans, t = [], 0.0
    for a, b in cuts:
        spans.append((t, a)); t = b
    spans.append((t, dur))
    words = []
    for (a, b), ws in zip(spans, chunks):
        tot = sum(len(w) + 1 for w in ws); x = a
        for w in ws:
            y = x + (b - a) * (len(w) + 1) / tot
            words.append((x, y, w.strip(".,!?;:—«»\"")))
            x = y
    return [w for w in words if w[2]]


def edge_voice(text, mp3):
    import edge_tts

    async def run():
        words, audio = [], bytearray()
        async for ch in edge_tts.Communicate(text, VOICE, rate="+8%", boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                audio.extend(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append((s, s + ch["duration"] / 1e7, ch["text"]))
        mp3.write_bytes(bytes(audio))
        return words
    return asyncio.run(run())


def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def subtitles(words, path, hook="", y=960, per=3):
    """So'zma-so'z subtitr (ASS): 3 so'zlik guruh, aytilayotgan so'z ko'k (#3d8bff).
    hook: birinchi 2.2 soniyada tepada ko'k quti ichida "sakrab" chiqadigan katta yozuv."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, "
            "Alignment, MarginL, MarginR, MarginV, Outline, Shadow\n"
            "Style: S,Anton,104,&H00FFFFFF,&H00000000,&H00000000,0,1,5,40,40,0,7,0\n"
            "Style: H,Anton,96,&H00FFFFFF,&H00FF8B3D,&H00000000,0,3,5,60,60,0,22,0\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Text\n")
    lines = []
    if hook:
        lines.append(rf"Dialogue: 1,0:00:00.00,0:00:02.20,H,{{\pos(540,120)\fscx135\fscy135"
                     rf"\t(0,180,\fscx100\fscy100)\fad(0,200)}}{hook.upper()}")
    for g in range(0, len(words), per):
        grp = words[g:g + per]
        for i, (s, e, _) in enumerate(grp):
            end = grp[i + 1][0] if i + 1 < len(grp) else (words[g + per][0] if g + per < len(words) else e + 0.4)
            txt = " ".join((r"{\c&H00FF8B3D&}" + w.upper() + r"{\c&H00FFFFFF&}") if j == i else w.upper()
                           for j, (_, _, w) in enumerate(grp))
            # har yangi guruh kichik "pop" bilan chiqadi
            pop = r"\fscx115\fscy115\t(0,120,\fscx100\fscy100)" if i == 0 else ""
            lines.append(rf"Dialogue: 0,{ass_time(s)},{ass_time(end)},S,{{\pos(540,{y}){pop}}}{txt}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def duration(path):
    out = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0


def speakable(text):
    """TTS uchun: emoji, yulduzcha, havolalarni olib tashlaydi; AI -> "ey-ay" emas, o'zbekcha "sun'iy intellekt" o'qiladi."""
    text = re.sub(r"https?://\S+|[*#]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)
    return re.sub(r"\s+", " ", text).strip()


MOVES = ("in", "out", "left", "right")
XFADES = ("slideleft", "smoothup", "circleopen", "zoomin", "wipeleft", "radial", "slideup", "fadeblack")


def clip_filter(idx, path, logo, frames, move):
    """Bitta rasm -> 1080xPH harakatli klip (zoompan): yaqinlashish/uzoqlashish/chapga/o'ngga surilish."""
    fit = (f"scale=-1:{PH - 300},pad={W}:{PH}:(ow-iw)/2:(oh-ih)/2:color=0x06122b" if logo
           else f"scale={W}:{PH}:force_original_aspect_ratio=increase,crop={W}:{PH}")
    z, x = {"in": (f"1+0.16*on/{frames}", "iw/2-(iw/zoom/2)"),
            "out": (f"1.16-0.16*on/{frames}", "iw/2-(iw/zoom/2)"),
            "left": ("1.18", f"(iw-iw/zoom)*(1-on/{frames})"),
            "right": ("1.18", f"(iw-iw/zoom)*on/{frames}")}[move]
    if "_karta" in pathlib.Path(path).name:  # matnli karta: chetidagi yozuv kesilmasin, faqat sekin yaqinlashadi
        z, x = f"1+0.04*on/{frames}", "iw/2-(iw/zoom/2)"
    return (f"[{idx}:v]{fit},scale={W * 2}:{PH * 2},setsar=1,"
            f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{PH}:fps=30,format=yuv420p[c{idx}]")


def make_reel(name, photos, h1_html, narration, hook="", fs=None, segments=None):
    """photos: rasm yo'llari ro'yxati (1-4 ta, har biri boshqa rasm) yoki None (logo).
    segments: [(matn, yorliq)] — har bo'lak o'z rasmi bilan (photos bilan teng), rasm aynan shu bo'lak
    aytilganda chiqadi, yorliq ("1-VOQEA") tepada sakrab chiqadi. Bunda narration = bo'laklar matni."""
    import math, random
    if fs is None:  # sarlavha uzunligiga qarab: panelga 3 qatorgacha sig'sin
        n = len(re.sub(r"<[^>]+>", "", h1_html))
        fs = 100 if n <= 45 else 86 if n <= 70 else 74
    OUT.mkdir(exist_ok=True)
    panel_html, panel_png = OUT / f"{name}_panel.html", OUT / f"{name}_panel.png"
    panel_html.write_text(PANEL % dict(fade=PH - 160, ph=PH, ph2=H - PH, fs=fs, h1=h1_html), encoding="utf-8")
    if not browser_shot(panel_html, panel_png, W, H, transparent=True):
        return None
    mp3 = OUT / f"{name}.mp3"
    if segments:
        narration = " ".join(t for t, _ in segments)
    words = voice(speakable(f"{hook}. {narration}" if hook else narration), mp3)
    if not mp3.exists() or not mp3.stat().st_size:
        return None
    ass = subtitles(words, OUT / f"{name}.ass", hook=hook)
    dur = duration(mp3) + 0.8
    starts = None
    if segments:  # har bo'lak boshlanish vaqti: so'zlar sonidan (ovoz so'z vaqtlari bo'yicha)
        idx = len(speakable(hook + ".").split()) if hook else 0
        starts, labels = [], []
        for t, lab in segments:
            starts.append(words[min(idx, len(words) - 1)][0] if words else 0)
            labels.append(lab)
            idx += len(speakable(t).split())
        starts[0] = 0.0
        extra = []
        for st, lab in zip(starts, labels):
            if lab:
                a = max(st, 2.3 if hook else 0)
                extra.append(rf"Dialogue: 1,{ass_time(a)},{ass_time(a + 1.8)},H,{{\pos(540,120)\fscx135\fscy135"
                             rf"\t(0,180,\fscx100\fscy100)\fad(0,200)}}{lab.upper()}")
        ass.write_text(ass.read_text(encoding="utf-8") + "\n".join(extra) + "\n", encoding="utf-8")
    if isinstance(photos, (str, pathlib.Path)):
        photos = [photos]
    logo = not photos
    photos = [pathlib.Path(p) for p in photos] if photos else [HERE / "logo" / "L1_evolving.png"]
    k, T = len(photos), 0.45
    seg = dur / k
    bounds = (starts + [dur]) if starts and len(starts) == k else [i * seg for i in range(k)] + [dur]
    moves = random.sample(MOVES, len(MOVES))
    parts, inputs = [], []
    for i, ph in enumerate(photos):
        length = bounds[i + 1] - bounds[i] + T
        inputs += ["-i", str(ph)]
        parts.append(clip_filter(i, ph, logo, math.ceil(length * 30) + 2, moves[i % len(moves)]))
    prev = "c0"
    for i in range(1, k):  # rasmlar orasida turli o'tish effektlari
        out = f"x{i}"
        parts.append(f"[{prev}][c{i}]xfade=transition={random.choice(XFADES)}:duration={T}:offset={max(bounds[i] - T / 2, 0.1):.3f}[{out}]")
        prev = out
    pi, ai = k, k + 1  # panel va ovoz kirish indekslari
    vf = ";".join(parts) + (
        f";[{prev}]pad={W}:{H}:0:0:black[bg];[bg][{pi}:v]overlay=0:0[v1];"
        f"color=c=0x3d8bff:s={W}x10:r=30[bar];[v1][bar]overlay=x='-{W}+{W}*t/{dur:.2f}':y={H - 10}:shortest=1[v2];"
        f"[v2]subtitles=filename='{esc(ass)}':fontsdir='{esc(HERE / 'fonts')}'[v]")
    # fon musiqasi (musiqa/ dagi CC0 treklardan tasodifiy, aylanib turadi): ovoz ostida past, oxirida so'nadi
    tracks = sorted((HERE / "musiqa").glob("*.mp3"))
    music = ["-stream_loop", "-1", "-i", str(random.choice(tracks))] if tracks else []
    if tracks:
        vf += (f";[{ai + 1}:a]volume=0.18,afade=t=in:d=0.3,afade=t=out:st={dur - 1.2:.2f}:d=1.2[m];"
               f"[{ai}:a]aresample=44100,volume=1.0[vo];[vo][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
               f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[a]")
    mp4 = OUT / f"{name}.mp4"
    cmd = [ffmpeg(), "-y", "-loglevel", "error", *inputs, "-i", str(panel_png), "-i", str(mp3),
           *music, "-filter_complex", vf, "-map", "[v]", "-map", "[a]" if tracks else f"{ai}:a",
           "-t", f"{dur:.2f}", "-r", "30",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode or not mp4.exists():
        print("ffmpeg xato:", r.stderr[-800:])
        return None
    return mp4
