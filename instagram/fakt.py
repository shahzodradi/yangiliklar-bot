"""Kunlik "Bilasizmi?" AI-fakt posti (AI.TezkorYangiliklari): mavzular.txt dagi navbatdagi Wikipedia maqolasidan
eng qiziq, kam ma'lum faktni oladi (Gemini so'zma-so'z iqtibos beradi va u matnda borligi tekshiriladi),
maqolaning sifatli rasmi bilan R3 uslubida PNG chizadi, rasm + izohni @malikalardekqizlargabot'ga yuboradi.
Ishlatish: python instagram/fakt.py [--quruq]"""
import json, re, sys, time, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import bot  # noqa: E402
import post as ig  # noqa: E402
import kunlik  # noqa: E402  (quality, uz, headline_html, PROMO, holat)

TOPICS = HERE / "mavzular.txt"
WIKI = "https://en.wikipedia.org/w/api.php?"
log = bot.log

PROMPT = """Quyida Wikipedia maqolasi ("{title}") matni bor. O'zbek tilidagi AI yangiliklari Instagram sahifasi uchun
undan BITTA eng qiziq, hayratlanarli, kam odam biladigan faktni tanla: o'quvchi "rostdanmi?!" deb, saqlab, do'stiga
yuboradigan bo'lsin. Quruq sana va ta'riflarni emas, kutilmagan detal, raqam yoki hikoyani ol.
Avval tanlangan faktlarni takrorlama: {used}

Faqat matnda aniq yozilgan narsani ol, hech narsa qo'shma va o'ylab topma.
- "iqtibos": fakt yozilgan gap(lar) matndan HARFMA-HARF nusxa (inglizcha, 1-2 gap).
- "sarlavha": o'zbekcha, 5-8 so'z (qisqa!), "Bilasizmi" so'zisiz, SHAXS yoki narsa NOMI bilan boshlansin (masalan "Alan Turing maktabga ..."), keyin faktning eng hayratlanarli qismi; bitta asosiy so'z yoki
  raqamni *yulduzcha* ichiga ol (qo'shimcha tashqarida: "*Deep Blue*ʼni").
- "fakt": o'zbekcha 2-3 gap: faktning o'zi, aniq raqam va nomlar bilan.
- "kontekst": o'zbekcha 1-2 gap: bu nega muhim yoki qiziq (faqat matndagi ma'lumot asosida).
- "savol": izohga javob yozdiradigan qisqa savol.
- "teglar": 5-7 heshteg, birinchisi #AI.
O'zbek lotin yozuvi, oʻ gʻ ʼ belgilari bilan, kirill harfisiz. Nomlarni tarjima qilma.

Faqat JSON: {{"iqtibos": "...", "sarlavha": "...", "fakt": "...", "kontekst": "...", "savol": "...", "teglar": ["#AI"]}}

MATN:
{text}
"""

SCHEMA = {"type": "OBJECT", "properties": {k: {"type": "STRING"} for k in
                                           ("iqtibos", "sarlavha", "fakt", "kontekst", "savol")} |
          {"teglar": {"type": "ARRAY", "items": {"type": "STRING"}}},
          "required": ["iqtibos", "sarlavha", "fakt", "kontekst", "savol", "teglar"]}


def wiki(params):
    return json.loads(bot.http(WIKI + urllib.parse.urlencode({"format": "json", "formatversion": 2, **params}),
                               timeout=30))


def article(title):
    """(matn, sahifa havolasi, [rasm url, ...]) — rasmlar: asosiy rasm + maqoladagi boshqa suratlar."""
    page = wiki({"action": "query", "prop": "extracts|pageimages|info", "titles": title, "explaintext": 1,
                 "piprop": "original", "inprop": "url", "redirects": 1})["query"]["pages"][0]
    text = page.get("extract", "")
    images = [page["original"]["source"]] if page.get("original") else []
    files = wiki({"action": "query", "generator": "images", "titles": page["title"], "gimlimit": 30,
                  "prop": "imageinfo", "iiprop": "url|size|mime", "iiurlwidth": 1600})
    for f in files.get("query", {}).get("pages", []):
        info = (f.get("imageinfo") or [{}])[0]
        if info.get("mime") in ("image/jpeg", "image/png") and info.get("width", 0) >= 1200:
            images.append(info.get("thumburl") or info["url"])
    return text, page.get("fullurl", ""), list(dict.fromkeys(images))


def best_image(urls):
    for url in urls[:8]:
        img = bot.download_image(url)
        q = kunlik.quality(img[0]) if img else None
        if q:
            w, h, sharp = q
            ok = w >= kunlik.MIN_W and h >= kunlik.MIN_H and sharp >= kunlik.MIN_SHARP
            log.info("Fakt rasmi %dx%d keskinlik %.0f %s", w, h, sharp, "OK" if ok else "sifatsiz")
            if ok:
                return img
    return None


def norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def caption(f, title, url):
    parts = [f"🤯 Bilasizmi? {kunlik.uz(re.sub(r'[*]', '', f['sarlavha']))}",
             f"📝 {kunlik.uz(f['fakt'])}",
             f"💡 {kunlik.uz(f['kontekst'])}",
             f"❓ {kunlik.uz(f['savol'])} Izohda yozing 👇\n📌 Saqlab qoʻying, 📤 doʻstingizga yuboring.",
             kunlik.PROMO,
             f"Manba: Wikipedia — {title} (maqoladagi manbalar bilan)\nRasm: Wikimedia Commons",
             " ".join(t if t.startswith("#") else f"#{t}" for t in f["teglar"][:7])]
    return "\n\n".join(parts)[:kunlik.CAPTION_MAX]


def main():
    dry = "--quruq" in sys.argv
    env = bot.load_env()
    topics = [t.strip() for t in TOPICS.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]
    state = kunlik.load_json(kunlik.IG_STATE, {"links": []})
    used = state.setdefault("faktlar", {})  # mavzu -> [olingan faktlar iqtiboslari]
    start = state.get("fakt_navbat", 0)
    for k in range(len(topics)):  # navbatdagi mavzu; matn/rasm bo'lmasa keyingisi
        i = (start + k) % len(topics)
        title = topics[i]
        try:
            text, url, images = article(title)
        except Exception as e:
            log.warning("Wikipedia xatosi (%s): %s", title, e)
            continue
        if len(text) < 1500:
            continue
        img = best_image(images)
        if not img:
            log.info("Sifatli rasm yo'q, keyingi mavzu: %s", title)
            continue
        f = bot.ask_models(env, PROMPT.format(title=title, used=used.get(title, []) or "yo'q",
                                              text=text[:14000]), SCHEMA, fast=True)
        if norm(f["iqtibos"])[:120] not in norm(text) or len(norm(f["iqtibos"])) < 40:
            log.warning("Iqtibos matnda topilmadi, fakt tashlandi: %s", f.get("iqtibos", "")[:120])
            continue
        data, ctype = img
        day = time.strftime("%Y-%m-%d")
        photo = kunlik.IMG_DIR / f"{day}_fakt_foto.{'png' if 'png' in ctype else 'jpg'}"
        kunlik.IMG_DIR.mkdir(exist_ok=True)
        photo.write_bytes(data)
        h1 = "Bilasizmi? " + kunlik.headline_html(kunlik.uz(f["sarlavha"]))
        png = ig.render(f"{day}_fakt", photo, h1)
        if not png:
            continue
        cap = caption(f, title, url)
        log.info("Fakt posti: %s | %s", title, f["sarlavha"])
        if dry:
            print(f"\n=== {png}\n{cap}\n")
            return
        if ig.send_post(*ig.ig_bot(), "🤯", png, cap):
            used.setdefault(title, []).append(f["iqtibos"][:200])
            state["fakt_navbat"] = (i + 1) % len(topics)
            kunlik.IG_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    log.warning("Bugun fakt posti chiqmadi")


if __name__ == "__main__":
    bot.setup_logging("instagram.log")
    main()
