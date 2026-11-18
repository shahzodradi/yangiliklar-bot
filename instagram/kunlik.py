"""Kunlik Instagram post (AI.TezkorYangiliklari): bot.py oxirgi 24 soatda yuborgan yangiliklardan eng yaxshilarini
tanlaydi, voqea rasmini topadi, R3 uslubida 1080x1350 PNG chizadi va rasm + tayyor izohni
@malikalardekqizlargabot'ga yuboradi. GitHub Actions: .github/workflows/instagram.yml (kuniga 1 marta).
Ishlatish: python instagram/kunlik.py [--quruq]   (--quruq: yubormaydi, faqat chizadi va chop etadi)"""
import html, io, json, re, sys, time, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import bot  # noqa: E402  (holat.json, Gemini, rasm yuklash funksiyalari)
import post as ig  # noqa: E402

IG_STATE = HERE / "ig_holat.json"
IMG_DIR = HERE / "chiqish"
COUNT = 2                 # kuniga nechta post
MIN_W, MIN_H = 1080, 700  # rasm sifati: asl o'lcham (1080x870 panelga cho'zilmasin)
MIN_SHARP = 300           # keskinlik (chegaralar dispersiyasi): kattalashtirilgan xira rasmlar ~200
WINDOW_HOURS = 26         # shu soat ichida bot yuborgan yangiliklar nomzod
CAPTION_MAX = 2200        # Instagram izoh limiti
PROMO = ("🤖 AI yangiliklarini har 2 soatda birinchi boʻlib olish uchun Telegram botimiz: "
         "@yangiliklaraivabiznes_bot")
log = bot.log

PROMPT = """Sen o'zbek tilidagi "AI.TezkorYangiliklari" Instagram sahifasi muharririsan. Auditoriya: AI bilan
video, rasm, musiqa yasaydigan kontent-meykerlar va AI'ga qiziquvchilar.
Quyidagi bugungi yangiliklardan Instagram uchun eng kuchli {count} tasini tanla: ko'p odamni qiziqtiradigan,
mashhur kompaniya yoki shaxs, katta raqam, janjal, kontent-meykerlarga bevosita ta'sir qiladigan voqealar.
Bir xil voqeani ikki marta olma.

Har biri uchun o'zbek tilida (lotin yozuvi, oʻ gʻ va ʼ belgilari bilan, kirill harfisiz) yoz:
- "sarlavha": 7-11 so'z, yangilik kanali uslubida, aniq va faqat berilgan faktlar asosida (o'ylab topma).
  Eng muhim BITTA so'zni yoki raqamni *yulduzcha* ichiga ol (masalan: "Anthropic kelasi hafta *birjaga* chiqmoqchi").
  Qo'shimcha yulduzchadan tashqarida qoladi: "*Suno*ʼni". "Sun'iy intellekt" o'rniga qisqa "AI" yoz.
- "emoji": sarlavhaga mos 1-2 emoji.
- "savol": izohga javob yozdiradigan qisqa savol (auditoriyaga murojaat, bahs uyg'otsin).
- "rasm": voqeadagi asosiy SHAXSning ism-familiyasi lotincha (Wikimedia'da surat qidirish uchun, masalan
  "Elvis Presley"). Yangilik kompaniya haqida bo'lsa, uning hozirgi rahbari (masalan Anthropic -> "Dario Amodei",
  OpenAI -> "Sam Altman"). Aniq shaxs bo'lmasa bo'sh qoldir. Faqat ism-familiya, boshqa so'z qo'shma.
- "hook": reel boshida 2 soniya katta chiqadigan va OVOZDA BIRINCHI aytiladigan 2-5 so'zli "ilmoq".
  Skrollni 1 soniyada to'xtatsin. Voqeaga eng mos BITTA formulani tanla (har xil postda har xil bo'lsin):
  1) Raqam zarbasi: "$100 mln bir kunda!", "10 baravar arzon!"
  2) Ism + shok fe'l: "Nvidia larzada!", "Google tan oldi!"
  3) Sizga ta'sir: "Endi telefoningiz ham...", "Rassomlar ishsiz qoladimi?"
  4) Taqiq/sir: "Buni hech kim aytmadi", "OpenAI yashirgan narsa"
  5) Kutilmagan qarama-qarshilik: "Bepul, lekin…", "Hamma xato qildi"
  6) Savol-ilmoq: "Suno endi kino ham qiladimi?"
  Faqat faktga asoslangan, yolg'on clickbait yo'q. Brend yoki raqam iloji bo'lsa hookda bo'lsin.
- "qiziqtir": reel ovozi uchun hookdan KEYIN aytiladigan 1-2 ta QISQA jumla (jami 12-20 so'z). Hookni takrorlama.
  Birinchi jumla eng qiziq faktni aytadi, ikkinchisi "ochiq halqa" qoldiradi — javobni bermaydi, odam postni
  o'qigisi keladi (masalan "Lekin eng qizig'i — narxi.", "Va bu hali boshlanishi."). Yolg'on va bo'rttirish yo'q.
- "teglar": 3-5 ta heshteg (# bilan): #AI va mavzuga aniq mos teglar (o'zbekcha va inglizcha aralash).
Brend va kompaniya nomlarini tarjima qilma.

Faqat JSON: {{"tanlov": [{{"id": 0, "sarlavha": "...", "emoji": "...", "savol": "...", "rasm": "...",
"teglar": ["#AI"]}}]}}

YANGILIKLAR (JSON):
{items}
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {"tanlov": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "sarlavha": {"type": "STRING"}, "emoji": {"type": "STRING"},
                       "savol": {"type": "STRING"}, "rasm": {"type": "STRING"}, "hook": {"type": "STRING"}, "qiziqtir": {"type": "STRING"}, "takror": {"type": "BOOLEAN"}, "voqea": {"type": "STRING"},
                       "teglar": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["id", "sarlavha", "emoji", "savol", "rasm", "teglar"]}}},
    "required": ["tanlov"],
}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def candidates(min_sources=2):
    """Bot yuborgan, 2+ nashrda tasdiqlangan, hali Instagram'ga chiqmagan yangiliklar."""
    done = set(load_json(IG_STATE, {}).get("links", []))
    items = [x for x in bot.load_state().get("kunlik", [])
             if x["t"] > time.time() - WINDOW_HOURS * 3600 and x["link"] not in done]
    verified = [x for x in items if len(set(x.get("domains") or [])) >= min_sources]
    log.info("IG nomzodlar: %d ta, 2+ manbada tasdiqlangan: %d ta", len(items), len(verified))
    return verified


# ---------- Rasm ----------

FREE = ("public domain", "cc0", "cc by", "pd")


def commons_photos(query, limit=3):
    """Wikimedia Commons'dan shu shaxs/joyning erkin litsenziyali rasmlari: [(url, kredit)]."""
    found = []
    if not query.strip():
        return found
    api = "https://commons.wikimedia.org/w/api.php?"
    q = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                                "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": 15, "prop": "imageinfo",
                                "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
    try:
        pages = json.loads(bot.http(api + q, timeout=20))["query"]["pages"].values()
    except Exception as e:
        log.info("Commons qidiruv xatosi: %s", e)
        return found
    words = [w.lower() for w in query.split() if len(w) > 1]
    if len(words) < 2:  # bitta so'z (kompaniya nomi) noto'g'ri rasm topadi: "Anthropic" -> "anthropomorphic"
        return found
    for p in sorted(pages, key=lambda p: p.get("index", 99)):
        title = p["title"].lower()
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not all(re.search(rf"\b{re.escape(w)}\b", title) for w in words) or not any(f in lic.lower() for f in FREE):
            continue
        if info.get("width", 0) < 1200 or info.get("height", 0) < MIN_H or title.endswith((".svg", ".gif")):
            continue
        if True:
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:60]
            artist = "" if "unknown" in artist.lower() else artist
            found.append((info.get("thumburl") or info["url"],
                          f"Wikimedia Commons, {lic}" + (f" ({artist})" if artist else "")))
    return found[:limit]


def quality(data):
    """(eni, bo'yi, keskinlik) yoki None (rasm ochilmasa)."""
    from PIL import Image, ImageFilter, ImageStat
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        g = im.convert("L")
        g.thumbnail((800, 800))
        return w, h, ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0]
    except Exception:
        return None


def bigger(url):
    """Sayt rasmining kattaroq nusxasi: WordPress -768x432, ?w=600 kabi o'lcham belgilarini olib tashlaydi."""
    no_size = re.sub(r"-\d{2,4}x\d{2,4}(?=\.\w{3,4}(\?|$))", "", url)
    no_query = re.sub(r"[?&](w|width|resize|h|height|fit|quality|q)=[^&]*", "", no_size)
    no_query = no_query.replace("&", "?", 1) if "?" not in no_query else no_query
    alts = [no_query, no_size]
    return [u for u in dict.fromkeys(alts) if u != url] + [url]


def fingerprint(data):
    """Rasm "barmoq izi" (8x8 o'rtacha hash): bir xil rasmning boshqa o'lcham/siqilgan nusxasini ham taniydi."""
    from PIL import Image
    try:
        g = Image.open(io.BytesIO(data)).convert("L").resize((8, 8))
        px = list(g.getdata())
        avg = sum(px) / 64
        return "%016x" % int("".join("1" if p > avg else "0" for p in px), 2)
    except Exception:
        return None


def similar(fp, used, max_diff=6):
    return any(bin(int(fp, 16) ^ int(u, 16)).count("1") <= max_diff for u in used)


def find_photos(item, query, used, n=4):
    """Sifatli va avval ishlatilmagan (used: barmoq izlari to'plami) rasmlar: [(data, ctype, kredit, iz)].
    Birinchisi eng yaxshisi (Wikimedia shaxs surati yoki eng kattasi). Sifatsiz rasm qo'shilmaydi."""
    cands = commons_photos(query, limit=6)
    if item.get("image"):
        cands += [(u, item["source"]) for u in bigger(item["image"])]
    for url in (item.get("urls") or [])[1:5]:
        og = bot.og_image(bot.fetch_html(url, timeout=10), url)
        if og:
            cands += [(u, bot.domain(url)) for u in bigger(og)]
    got, seen = [], set()
    for url, credit in dict.fromkeys(cands):
        if len(got) >= n:
            break
        img = bot.download_image(url)
        q = quality(img[0]) if img else None
        if not q:
            continue
        w, h, sharp = q
        fp = fingerprint(img[0])
        if not fp or not (w >= MIN_W and h >= MIN_H and sharp >= MIN_SHARP):
            continue
        if similar(fp, used) or similar(fp, seen):
            log.info("Rasm avval ishlatilgan, o'tkazildi: %s", bot.domain(url))
            continue
        seen.add(fp)
        got.append((img[0], img[1], credit, fp, (credit.startswith("Wikimedia"), w * h)))
    got.sort(key=lambda g: g[4], reverse=True)
    return [g[:4] for g in got]


def find_photo(item, query):
    """Eng sifatli rasm: Wikimedia (shaxs) + asosiy va boshqa nashrlar maqolalaridagi rasmlar.
    Kamida MIN_W x MIN_H va MIN_SHARP keskinlikdan o'tmasa — None (sifatsiz rasm qo'yilmaydi)."""
    cands = commons_photos(query)
    if item.get("image"):
        cands += [(u, item["source"]) for u in bigger(item["image"])]
    for url in (item.get("urls") or [])[1:4]:  # boshqa nashrlardagi maqolalarning og:image rasmi
        og = bot.og_image(bot.fetch_html(url, timeout=10), url)
        if og:
            cands += [(u, bot.domain(url)) for u in bigger(og)]
    best = None
    for url, credit in dict.fromkeys(cands):
        img = bot.download_image(url)
        q = quality(img[0]) if img else None
        if not q:
            continue
        w, h, sharp = q
        ok = w >= MIN_W and h >= MIN_H and sharp >= MIN_SHARP
        log.info("Rasm %s: %dx%d keskinlik %.0f %s", bot.domain(url), w, h, sharp, "OK" if ok else "sifatsiz")
        if ok and (best is None or (credit.startswith("Wikimedia"), w * h) > best[0]):
            best = ((credit.startswith("Wikimedia"), w * h), img, credit)
        if best and best[2].startswith("Wikimedia"):
            break  # shaxsning erkin litsenziyali sifatli surati — eng yaxshisi
    return (best[1], best[2]) if best else (None, None)


# ---------- Izoh ----------

def build_caption(item, pick, photo_credit):
    title = re.sub(r"\*", "", pick["sarlavha"]).strip()
    sources = list(dict.fromkeys([item["source"], *(item.get("domains") or [])]))

    def build(expert_limit):
        parts = [f"{title} {pick['emoji']}".strip(), f"📝 {item['text']}"]
        if item.get("experts") and expert_limit:
            lines = ["🎓 Mutaxassislar fikri"]
            for v in item["experts"]:
                fikr = bot.shorten(v["fikr"], expert_limit)
                lines.append(f"• {v['kim']}, {v['lavozim']}: {fikr} ({v['domain']})")
            parts.append("\n".join(lines))
        if item.get("effect"):
            parts.append(f"➡️ Nimaga olib kelishi mumkin\n{item['effect']}")
        parts.append(f"❓ {pick['savol']} Izohda yozing 👇\n📌 Saqlab qoʻying, 📤 doʻstingizga yuboring.")
        parts.append(PROMO)
        parts.append(f"Manbalar: {', '.join(sources)}" + (f"\nRasm: {photo_credit}" if photo_credit else ""))
        parts.append(" ".join(t if t.startswith("#") else f"#{t}" for t in pick["teglar"][:7]))
        return "\n\n".join(parts)

    for limit in (300, 200, 120, 0):
        caption = build(limit)
        if len(caption) <= CAPTION_MAX:
            return caption
    return caption[:CAPTION_MAX]


def uz(text):
    """Apostroflarni o'zbek lotin belgilariga: Suno'ni -> Sunoʼni, o' g' -> oʻ gʻ, qolgan ' -> ʼ."""
    text = re.sub(r"\b([A-Z][\w.]*)['‘’`](ni|ning|ga|da|dan|lar|dagi|ga|day)\b", r"\1ʼ\2", text or "")
    text = re.sub(r"([oOgG])['‘’`]", r"\1ʻ", text)
    return re.sub(r"['’`]", "ʼ", text)


def headline_html(text):
    text = html.escape(text.strip())
    if "*" not in text:
        return text
    return re.sub(r"\*([^*]+)\*", r"<b>\1</b>", text, count=1).replace("*", "")


# ---------- Videolar ----------

VIDEO_MAX = 3


def items_all():
    return [x for x in bot.load_state().get("kunlik", []) if x["t"] > time.time() - WINDOW_HOURS * 3600]


def send_videos(items, state, token, chat, dry):
    """Kunning AI videolari: yangiliklar ichidagi video havolalari + reel uchun tayyor izoh (muallif ko'rsatilgan)."""
    done = set(state.setdefault("videos", []))
    picked = []
    for x in sorted(items, key=lambda x: -x.get("baho", 0)):
        if x["soha"] not in ("AI tanlov", "AI video", "AI musiqa", "AI rasm"):
            continue
        for url, kind in x.get("videos") or []:
            if url not in done and all(url != p[1] for p in picked):
                picked.append((x, url, kind))
                break
        if len(picked) >= VIDEO_MAX:
            break
    log.info("Kunning videolari: %d ta", len(picked))
    for n, (x, url, kind) in enumerate(picked, 1):
        text = (f"🎬 Video {n} — {kind}\n{url}\n\n"
                f"✍️ Reel izohi:\n{x['title']} 🎬\n\n📝 {x['text']}\n\n"
                f"🎥 Video muallifi: {kind} ({url})\nManba: {x['source']}\n\n{PROMO}\n\n#AI #AIvideo #suniyintellekt")
        if dry:
            print(text, "\n")
            continue
        if ig.tg(token, "sendMessage", {"chat_id": chat, "text": text}):
            state["videos"] = (state["videos"] + [url])[-300:]


# ---------- Har bir yangilik -> Instagram (avtomatik, har soatda) ----------

HAR_MAX_RUN = 25    # bir ishga tushishda (bot bir soatda bundan ko'p yubormaydi)
HAR_MAX_DAY = 48    # sutkada: Instagram API'ning qat'iy limiti 50 post/24 soat
REEL_MAX_DAY = 24   # sutkada nechta yangilik reel bo'lib ham chiqadi (har yangilik; IG limiti 50 post/sutka)
IMG_MAX_DAY = 5     # rasmli post sutkada 5 ta (rasmlar 0 ko'rish oldi, reellar ~120): faqat eng kuchli, haqiqiy rasmli
IMG_MAX_RUN = 1     # bir ishga tushishda bitta rasm — kun bo'yi tarqalsin, spamdek ko'rinmasin
HAR_WINDOW = 6 * 3600
# hookdan keyin darhol: obuna bo'lsa nima oladi (obunachisiz sahifa uchun eng muhim jumla)
OBUNA = "Bunaqa yangiliklarni oʻtkazib yubormaslik uchun obuna boʻling!"


def har():
    """Bot yuborgan HAR BIR yangilik (manba soni farqi yo'q) -> R3 post -> Instagram'ga darhol (tugmasiz)."""
    dry = "--quruq" in sys.argv
    env = bot.load_env()
    token, chat = (None, None) if dry else ig.ig_bot()
    state = load_json(IG_STATE, {"links": []})
    start = state.setdefault("har_start", time.time() - 3600)  # birinchi ishga tushishda eski navbat yuborilmaydi
    done = set(state["links"])
    now = time.time()
    posted = [t for t in state.get("har_vaqt", []) if t > now - 86400]
    items = [x for x in bot.load_state().get("kunlik", [])
             if x["t"] > max(start, now - HAR_WINDOW) and x["link"] not in done]
    if dry and not items:  # quruq sinov: oxirgi 2 ta yangilik bilan chizib ko'rish
        items = bot.load_state().get("kunlik", [])[-2:]
    room = min(HAR_MAX_RUN, HAR_MAX_DAY - len(posted))
    log.info("Har-yangilik: %d ta yangi, joy %d", len(items), room)
    if not items or room <= 0:
        return
    items = items[:room]
    brief = [{"id": i, "sarlavha": x["title"], "mazmun": x["text"], "soha": x["soha"]} for i, x in enumerate(items)]
    prompt = PROMPT.replace("Instagram uchun eng kuchli {count} tasini tanla: ko'p odamni qiziqtiradigan,\n"
                            "mashhur kompaniya yoki shaxs, katta raqam, janjal, kontent-meykerlarga bevosita ta'sir "
                            "qiladigan voqealar.\nBir xil voqeani ikki marta olma.",
                            "HAR BIRI uchun ({count} ta, hammasini, tashlab ketma) Instagram posti yoz.")
    # Bir voqea turli saytlardan bir necha marta keladi: Gemini takrorni belgilaydi (shu ro'yxatda va oxirgi 48 soatda)
    mavzular = [m for m in state.get("mavzular", []) if m[0] > now - 48 * 3600]
    prompt = prompt.format(count=len(items), items=json.dumps(brief, ensure_ascii=False)) + (
        "\nTAKRORNI TEKSHIR: yangilik ro'yxatdagi kichikroq id'li yangilik bilan yoki quyidagi AVVAL JOYLANGAN "
        "voqealardan biri bilan BIR XIL voqea bo'lsa (boshqa sayt, boshqa so'zlar bilan yozilgan bo'lsa ham), "
        "\"takror\": true qo'y. Bir kompaniyaning boshqa-boshqa voqealari takror emas.\n"
        "Har biriga \"voqea\" ham yoz: voqeaning qisqa inglizcha kaliti, kichik harfda, 2-3 so'z: kompaniya + "
        "mahsulot/harakat (masalan \"suno visuals launch\", \"openai daybreak nato\"). Bir voqea haqidagi "
        "yangiliklarga AYNAN BIR XIL kalit ber.\nAVVAL JOYLANGAN:\n"
        + json.dumps([m[1] for m in mavzular[-80:]], ensure_ascii=False))
    result = bot.ask_models(env, prompt, SCHEMA, fast=True)
    keys = {m[2] for m in mavzular if len(m) > 2 and m[2]}  # voqea kalitlari (48 soat): bir voqea bir marta
    picks = {p["id"]: p for p in result.get("tanlov", []) if isinstance(p.get("id"), int) and 0 <= p["id"] < len(items)}
    IMG_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M")
    reel_cands = []
    used = set(state.get("rasmlar", []))  # ishlatilgan rasmlarning barmoq izlari: bir xil rasm qayta chiqmaydi
    for i, item in enumerate(items):
        state["links"] = (state["links"] + [item["link"]])[-300:]
        pick = picks.get(i) or {  # Gemini sarlavha bermasa ham yangilik tashlab ketilmaydi
            "sarlavha": item["title"], "emoji": "⚡", "savol": "Bu haqda nima deysiz?", "rasm": "",
            "teglar": ["#AI", "#suniyintellekt", "#AIyangiliklar", "#texnologiya", "#AITezkor"]}
        pick.update({k: uz(pick.get(k, "")) for k in ("sarlavha", "savol")})
        key = re.sub(r"[^a-z0-9 ]", "", str(pick.get("voqea", "")).lower()).strip()
        if pick.get("takror") or (key and key in keys):
            log.info("Takror voqea, o'tkazildi: %s", item["title"])
            continue
        keys.add(key)
        mavzular.append([now, item["title"], key])
        found = find_photos(item, pick.get("rasm", ""), used, n=4)  # avval ishlatilgan rasmlar o'tkaziladi
        files, credit = [], None
        for j, (data, ctype, cr, fp) in enumerate(found):
            f = IMG_DIR / f"{stamp}_{i}_foto{j}.{'png' if 'png' in ctype else 'jpg'}"
            f.write_bytes(data)
            files.append(f)
            used.add(fp)
            credit = credit or cr
        photo = files[0] if files else None  # sifatli yangi rasm topilmasa: logo kartasi
        if not files:
            log.info("Sifatli yangi rasm yo'q — logo kartasi bilan: %s", item["link"])
        png = ig.render(f"{stamp}_{i}", photo, headline_html(pick["sarlavha"]))
        if not png:
            continue
        caption = build_caption(item, pick, credit)
        log.info("IG avto: %s (%d rasm)", pick["sarlavha"], len(files))
        # har yangilik reel bo'ladi; reel postdagi rasmni takrorlamasin: 3+ rasm bo'lsa birinchisisiz (rasmsiz -> logo)
        reel_cands.append((item.get("baho", 0) + len(files), i, item, pick,
                           (files[1:] if len(files) >= 3 else files) or None, caption, png if files else None))
        if dry:
            print(f"\n=== {png}\n{caption}\n")
    # Har bir yangilik reel ham bo'ladi (reel'lar obunachi bo'lmaganlarga ham tarqaladi), eng kuchlisi birinchi
    reels = [t for t in state.get("reel_vaqt", []) if t > now - 86400]

    def save():
        state["reel_vaqt"], state["har_vaqt"], state["rasmlar"] = reels, posted, list(used)[-2000:]
        state["mavzular"] = mavzular[-150:]
        if not dry:  # har qadamdan keyin: job uzilsa ham joylangan narsa qayta joylanmaydi
            IG_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    save()
    import reel
    for _, i, item, pick, photo, caption, _png in sorted(reel_cands, key=lambda c: -c[0]):
        if len(reels) >= REEL_MAX_DAY or len(posted) >= HAR_MAX_DAY:
            break
        title = re.sub(r"\*", "", pick["sarlavha"]).strip()
        tease = uz(pick.get("qiziqtir", "")).strip() or title  # qisqa: qiziqtiradi, tafsilot izohda
        narration = f"{OBUNA} {tease.rstrip('.!')}. Batafsil izohda oʻqing."
        mp4 = reel.make_reel(f"{stamp}_{i}_reel", photo, headline_html(pick["sarlavha"]), narration,
                             hook=uz(pick.get("hook", "")).strip(" .!") + "!" if pick.get("hook") else "")
        log.info("Reel: %s -> %s", title, mp4)
        if mp4 and not dry and ig.publish_reel(token, chat, mp4, caption):
            reels.append(time.time())
            posted.append(time.time())
            save()
        if dry:
            break  # sinovda bitta reel yetadi
    # Rasmli post: reellardan keyin, faqat eng kuchli haqiqiy rasmli yangilik, sutkada IMG_MAX_DAY ta
    imgs = [t for t in state.get("img_vaqt", []) if t > now - 86400]
    for _, i, item, pick, photo, caption, png in sorted(reel_cands, key=lambda c: -c[0])[:IMG_MAX_RUN]:
        if dry or not png or len(imgs) >= IMG_MAX_DAY or len(posted) >= HAR_MAX_DAY:
            break
        if ig.publish_post(token, chat, png, caption):
            imgs.append(time.time())
            posted.append(time.time())
    state["img_vaqt"] = imgs
    save()


# ---------- Kechki TOP-3 reel (Toshkent 20:00) ----------

TOP3_PROMPT = """Sen o'zbek tilidagi "AI.TezkorYangiliklari" Instagram sahifasi muharririsan.
Quyida bugun (oxirgi 24 soat) yuborilgan AI yangiliklari bor. Kechki "Bugun AI'da TOP-3" reeli uchun ODDIY
ODAMLARNI (faqat mutaxassislarni emas) eng ko'p qiziqtiradigan 3 ta TURLI voqeani tanla. Ustunlik: hamma biladigan
nomlar (ChatGPT, OpenAI, Google, Apple, Elon Musk, Nvidia...), katta pul va raqamlar, janjal va kutilmagan voqealar,
odamlarning ishi, puli yoki telefoniga ta'sir qiladiganlar, bepul yangi imkoniyatlar, O'zbekistonga oid AI yangiligi.
Texnik, tor sohaviy yoki zerikarli korporativ xabarlarni olma. Bir voqeaning ikki maqolasini olma. O'zbek tilida (lotin, oʻ gʻ ʼ belgilari bilan, kirillsiz) yoz:
- "sarlavha": 4-7 so'zli qisqa sarlavha (ovozda o'qiladi), faqat berilgan faktlar.
- "gap": bitta JUDA qisqa gap (6-10 so'z): eng qiziq fakt yoki raqam, faqat berilgan faktlar.
Reel 25 soniyadan oshmasin: ortiqcha so'z yozma.
- "rasm": voqeadagi asosiy shaxsning ism-familiyasi lotincha (kompaniya bo'lsa uning rahbari), bo'lmasa bo'sh.
Umumiy: "hook" — reel boshida aytiladigan va katta chiqadigan 3-6 so'zli ilmoq: eng kuchli voqeaning eng
shok fakti (raqam, mashhur ism yoki "sizga ta'siri"), masalan "OpenAI hammani hayratga soldi!", "Endi ChatGPT
bepul emasmi?". Faqat faktga asoslangan. "savol" — izohga yozdiradigan 3-6 so'zli savol; "teglar" — 3-5 heshteg (#AI bilan).

YANGILIKLAR (JSON):
{items}
"""

TOP3_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "top": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER"}, "sarlavha": {"type": "STRING"}, "gap": {"type": "STRING"},
            "rasm": {"type": "STRING"}}, "required": ["id", "sarlavha", "gap", "rasm"]}},
        "hook": {"type": "STRING"}, "savol": {"type": "STRING"},
        "teglar": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["top", "hook", "savol", "teglar"],
}


def top3():
    """Kunning eng muhim 3 voqeasi bitta reelda: har voqea o'z rasmi va "1-VOQEA" yorlig'i bilan."""
    dry = "--quruq" in sys.argv
    state = load_json(IG_STATE, {"links": []})
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 5 * 3600))  # Toshkent sanasi
    if state.get("top3_kun") == today and not dry:
        return log.info("TOP-3 bugun allaqachon chiqqan")
    now = time.time()
    items = [x for x in bot.load_state().get("kunlik", []) if x["t"] > now - 86400]
    if len(items) < 3:
        return log.info("TOP-3 uchun yangilik kam: %d", len(items))
    items = sorted(items, key=lambda x: -(x.get("baho") or 0))[:40]
    brief = [{"id": i, "sarlavha": x["title"], "mazmun": x["text"], "soha": x["soha"]} for i, x in enumerate(items)]
    res = bot.ask_models(bot.load_env(), TOP3_PROMPT.format(items=json.dumps(brief, ensure_ascii=False)),
                         TOP3_SCHEMA, fast=True)
    top = [t for t in res.get("top", []) if isinstance(t.get("id"), int) and 0 <= t["id"] < len(items)][:3]
    if len(top) < 3:
        return log.info("TOP-3: Gemini 3 ta voqea bermadi")
    used = set(state.get("rasmlar", []))
    stamp = time.strftime("%Y-%m-%d_%H%M")
    IMG_DIR.mkdir(exist_ok=True)
    photos, credits = [], []
    import reel
    for n, t in enumerate(top):
        found = find_photos(items[t["id"]], t.get("rasm", ""), used, n=1)
        if found:
            data, ctype, cr, fp = found[0]
            f = IMG_DIR / f"{stamp}_top3_{n}.{'png' if 'png' in ctype else 'jpg'}"
            f.write_bytes(data)
            used.add(fp)
            photos.append(f)
            credits.append(cr)
        else:  # yangi sifatli rasm yo'q: raqam + sarlavha kartasi (rasm takrorlanmasin, logo emas)
            c = reel.title_card(f"{stamp}_top3_karta{n}", n + 1, uz(t["sarlavha"]).strip(" .!").replace("*", ""))
            photos.append(c or HERE / "logo" / "L1_evolving.png")
    s = [(uz(t["sarlavha"]).strip(" .!"), uz(t["gap"]).strip()) for t in top]
    # ~25 s: ovozda gap faqat qisqa bo'lsa o'qiladi (to'liq matn izohda), uzun bo'lsa faqat sarlavha
    say = [f"{a}." for a, b in s]  # ovozda faqat sarlavhalar (~20 s); fakt va tafsilot izohda
    savol = uz(res.get("savol", "")).strip() or "Qaysi biri sizni hayratga soldi?"
    segments = [
        (f"Bugungi eng qiziq uchtasi. {OBUNA} Birinchi: {say[0]}", "1-voqea"),
        (f"Ikkinchi: {say[1]}", "2-voqea"),
        (f"Uchinchi: {say[2]} {savol} Izohda yozing.", "3-voqea"),
    ]
    mp4 = reel.make_reel(f"{stamp}_top3", photos, headline_html("Bugun AI'da *TOP-3* voqea"), "",
                         hook=(uz(res.get("hook", "")).strip(" .!") + "!") if res.get("hook") else "Bugun AI'da TOP-3",
                         segments=segments, eleven=True)
    log.info("TOP-3 reel: %s -> %s", [x[0] for x in s], mp4)
    if not mp4:
        return
    tags = " ".join(t if t.startswith("#") else "#" + t for t in res.get("teglar", [])[:5]) or "#AI"
    caption = (f"| Bugun AI'da TOP-3 voqea 🔥\n\n"
               + "\n\n".join(f"{e} {a}\n{b}" for e, (a, b) in zip(("1️⃣", "2️⃣", "3️⃣"), s))
               + f"\n\n❓ {savol} Izohda yozing 👇\n📌 Saqlab qoʻying, 📤 doʻstingizga yuboring.\n\n"
               + (f"📷 {', '.join(dict.fromkeys(credits))}\n\n" if credits else "") + tags)
    if dry:
        return print(caption)
    token, chat = ig.ig_bot()
    if ig.publish_reel(token, chat, mp4, caption):
        state = load_json(IG_STATE, {"links": []})  # har() shu orada yozgan bo'lishi mumkin
        state["top3_kun"] = today
        state["rasmlar"] = list(set(state.get("rasmlar", [])) | used)[-2000:]
        state["reel_vaqt"] = state.get("reel_vaqt", []) + [time.time()]
        state["har_vaqt"] = state.get("har_vaqt", []) + [time.time()]
        IG_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- Asosiy ----------

def main():
    if "--har" in sys.argv:
        return har()
    if "--top3" in sys.argv:
        return top3()
    if "--video" in sys.argv:  # 19:00 da faqat kunning videolari (postlarni endi har() joylaydi)
        state = load_json(IG_STATE, {"links": []})
        token, chat = ig.ig_bot()
        send_videos(items_all(), state, token, chat, False)
        IG_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    dry = "--quruq" in sys.argv
    test = "--sinov" in sys.argv  # GitHub'da tekshirish: 1 manba ham bo'laveradi, holat saqlanmaydi
    env = bot.load_env()
    token, chat = (None, None) if dry else ig.ig_bot()
    items = candidates(1 if test else 2)
    if not items:
        if not dry:
            ig.tg(token, "sendMessage", {"chat_id": chat, "text": "📭 Bugun 2+ manbada tasdiqlangan yangi "
                                         "AI yangilik topilmadi — Instagram posti yoʻq."})
        return
    brief = [{"id": i, "sarlavha": x["title"], "mazmun": x["text"], "soha": x["soha"], "baho": x["baho"],
              "mutaxassis": len(x.get("experts") or []), "manbalar": len(set(x.get("domains") or []))}
             for i, x in enumerate(items)]
    result = bot.ask_models(env, PROMPT.format(count=COUNT + 3, items=json.dumps(brief, ensure_ascii=False)),
                            SCHEMA, fast=True)
    picks = [p for p in result.get("tanlov", []) if isinstance(p.get("id"), int) and 0 <= p["id"] < len(items)]
    picks = list({p["id"]: p for p in picks}.values())  # rasm topilmasa keyingisi olinadi
    IMG_DIR.mkdir(exist_ok=True)
    state = load_json(IG_STATE, {"links": []})
    day = time.strftime("%Y-%m-%d")
    n = 0
    for pick in picks:
        if n >= COUNT:
            break
        item = items[pick["id"]]
        pick.update({k: uz(pick.get(k, "")) for k in ("sarlavha", "savol")})
        img, credit = find_photo(item, pick.get("rasm", ""))
        if not img:
            log.warning("Sifatli rasm topilmadi, keyingi yangilik olinadi: %s", item["link"])
            continue
        n += 1
        data, ctype = img
        photo = IMG_DIR / f"{day}_{n}_foto.{'png' if 'png' in ctype else 'jpg'}"
        photo.write_bytes(data)
        png = ig.render(f"{day}_{n}", photo, headline_html(pick["sarlavha"]))
        if not png:
            log.warning("PNG chizilmadi: %s", item["link"])
            continue
        caption = ("🧪 SINOV (tekshirilmagan bo'lishi mumkin)\n\n" if test else "") + build_caption(item, pick, credit)
        log.info("IG post %d: %s (%d belgi, rasm: %s)", n, pick["sarlavha"], len(caption), credit)
        if dry:
            print(f"\n=== {png}\n{caption}\n")
            continue
        if ig.send_post(token, chat, n, png, caption):
            state["links"] = (state["links"] + [item["link"]])[-200:]
    if not test:
        send_videos(items_all(), state, token, chat, dry)
    if not dry and not test:
        IG_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    bot.setup_logging("instagram.log")
    main()
