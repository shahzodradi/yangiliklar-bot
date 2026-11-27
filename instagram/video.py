"""Yangilik uchun haqiqiy video parchalar (reelda rasm o'rniga): maqoladagi videolar (YouTube/X/Vimeo),
keyin YouTube'dan shu voqea haqidagi yangi video. Har nomzod tekshiriladi: voqeaga mos sarlavha, katta/rasmiy
kanal, yangi, tiniq (>=720p). Faqat qisqa bo'lak (ovozsiz) yuklanadi."""
import datetime, json, logging, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import bot  # noqa: E402

log = logging.getLogger("instagram")
CLIP_S = 9          # bitta parcha uzunligi (s)
MIN_H = 720         # bundan past sifatli video olinmaydi (xira bo'lmasin)
MAX_AGE_DAYS = 14   # shundan eski video boshqa voqea bo'lishi mumkin
MIN_SUBS = 200_000  # YouTube: faqat katta/rasmiy kanallar (kichik "AI news" kanallari matnli kartalar qo'yadi)
JUNK = re.compile(r"ai[- ]generated|#shorts|reaction|hindi|tamil|telugu|urdu|tutorial|how to |explained", re.I)
STOP = set("the and for with from that this into over after about their will have says said new more than its "
           "your what when how why are was were been being just also amid while video watch news today".split())


def words(text):
    return {w for w in re.findall(r"[a-z0-9$]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def ydl_info(url, extra=None):
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, **(extra or {})}
    with yt_dlp.YoutubeDL(opts) as y:
        return y.extract_info(url, download=False)


def candidates(item):
    """Tekshirilmagan nomzod havolalar: maqola ichidagi videolar + YouTube qidiruv natijalari."""
    out = [u for u, _ in (item.get("videos") or [])]
    if not out:
        for url in [item["link"], *(item.get("urls") or [])[1:3]]:
            out += [u for u, _ in bot.video_links(bot.fetch_html(url, timeout=10), limit=4)]
    try:
        res = ydl_info(f"ytsearch6:{item.get('orig_title') or item['title']}", {"extract_flat": "in_playlist"})
        out += [f"https://www.youtube.com/watch?v={e['id']}" for e in res.get("entries") or [] if e.get("id")]
    except Exception as e:
        log.info("YouTube qidiruv ishlamadi: %s", str(e)[:120])
    return list(dict.fromkeys(out))


def suitable(info, want):
    """Voqeaga mos va sifatli manbami: (ha/yo'q, sabab)."""
    text = f"{info.get('title') or ''} {(info.get('description') or '')[:400]}"
    common = want & words(text)
    since = (datetime.date.today() - datetime.timedelta(days=MAX_AGE_DAYS)).strftime("%Y%m%d")
    if info.get("upload_date") and info["upload_date"] < since:
        return False, "eski"
    if JUNK.search(info.get("title") or ""):
        return False, "keraksiz tur"
    dur = info.get("duration") or 0
    if dur and not 5 <= dur <= 1800:
        return False, f"davomiylik {dur}"
    if "youtube" in (info.get("extractor") or "").lower():
        if (info.get("channel_follower_count") or 0) < MIN_SUBS:
            return False, f"kichik kanal {info.get('channel')}"
        if len(common) < 3:
            return False, f"mos emas: {info.get('title')}"
    elif len(common) < 1:  # X/Vimeo: post matnida voqeadan kamida bitta so'z (brend) bo'lsin
        return False, f"mos emas: {(info.get('title') or '')[:60]}"
    return True, f"{info.get('channel') or info.get('uploader')}: {info.get('title')}"


def download(info, dest):
    """Videoning qisqa tiniq bo'lagini yuklaydi -> mp4 yo'li yoki None."""
    import yt_dlp
    from yt_dlp.utils import download_range_func
    dur = info.get("duration") or 0
    start = min(dur * 0.15, 20) if dur > CLIP_S + 5 else 0
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "outtmpl": str(dest) + ".%(ext)s",
            "format": f"bv*[height>={MIN_H}][vcodec^=avc1]/bv*[height>={MIN_H}]/b[height>={MIN_H}]",
            "download_ranges": download_range_func(None, [(start, start + CLIP_S)]),
            "force_keyframes_at_cuts": True}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([info.get("webpage_url")])
    except Exception as e:
        log.info("Video yuklanmadi: %s", str(e)[:100])
        return None
    got = sorted(dest.parent.glob(dest.name + ".*"))
    if not got:
        return None
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=height:format=duration", "-of", "json", str(got[0])], capture_output=True, text=True)
    try:
        p = json.loads(probe.stdout)
        ok = p["streams"][0]["height"] >= MIN_H and float(p["format"]["duration"]) >= 3
    except Exception:
        ok = False
    if not ok:
        got[0].unlink(missing_ok=True)
        return None
    return got[0]


def find_clips(item, used, dest_dir, stamp, n=2):
    """Yangilik uchun n tagacha video parcha: [(mp4_yo'li, havola, manba)]. used: avval ishlatilgan havolalar."""
    want = words(item.get("orig_title") or "") | words(item["title"])
    clips = []
    for url in candidates(item):
        if len(clips) >= n:
            break
        if url in used:
            continue
        try:
            info = ydl_info(url)
            if info.get("_type") == "playlist":
                info = next((e for e in info.get("entries") or [] if e), None) or {}
        except Exception:
            continue  # videosiz post yoki yopiq video
        ok, why = suitable(info, want)
        if not ok:
            log.info("Video o'tkazildi (%s): %s", why, url)
            continue
        f = download(info, dest_dir / f"{stamp}_v{len(clips)}")
        if f:
            log.info("Video parcha: %s — %s", url, why)
            clips.append((f, url, why.split(":")[0]))
    return clips
