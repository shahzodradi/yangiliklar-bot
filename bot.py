"""Yangiliklar bot: RSS manbalardan yangiliklarni yig'adi, Gemini bilan saralab
o'zbekchaga qisqa qilib yozadi va Telegram'ga yuboradi.

Ishga tushirish:
    python bot.py            # odatiy ish (Task Scheduler shuni chaqiradi)
    python bot.py --dry      # yubormasdan, natijani ekranga chiqaradi
    python bot.py --chat-id  # botga /start yozgandan keyin chat ID ni .env ga yozadi
    python bot.py --sozla    # token va kalitni kiritish (tekshirib saqlaydi)
    python bot.py --tinglash # Telegram'dagi "Hozirgi yangiliklar" tugmasini kutadi (fon jarayoni)
    python bot.py --tugma ID CHAT [ai|video]  # bulutda: tugma/buyruq (⏳ xabar ID si, u turgan chat, mavzu)
"""
import calendar
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

import feedparser

DIR = Path(__file__).resolve().parent
ENV_FILE = DIR / ".env"
STATE_FILE = DIR / "holat.json"
SOURCES_FILE = DIR / "manbalar.txt"
PROFILE_FILE = DIR / "profil.txt"
LOCK_FILE = DIR / "ishlayapti.lock"
GROUPS_FILE = DIR / "guruhlar.json"  # bot qo'shilgan guruhlar (Cloudflare Worker + guruh.yml yangilaydi)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
MAX_AGE_HOURS = 24          # bugungi yangiliklar: oxirgi 24 soat
FALLBACK_AGE_HOURS = 48     # bugungilarning hammasi yuborilgan bo'lsa, shuncha soatgacha olinadi
SENT_KEEP_HOURS = 36        # takrorlamaslik uchun yuborilgan sarlavhalar shuncha saqlanadi
PER_SOURCE_LIMIT = 8        # bitta manbadan bir martada ko'pi bilan nechta
MAX_CANDIDATES = 200        # Gemini'ga yuboriladigan nomzodlar soni
MAX_MEDIA = 8               # bir martada AI video/rasm/musiqa yangiliklari: hammasi, shuncha tagacha
MAX_OTHER = 5               # bir martada qolgan AI yangiliklaridan (har qanday AI mavzusi), shuncha tagacha
SELECT_POOL = MAX_MEDIA + MAX_OTHER + 2  # Gemini shuncha tagacha tanlaydi (tanlovlar bundan tashqari ham kirishi mumkin)
# Mavzu buyruqlari: faqat shu mavzudagi yangiliklar, faqat so'ragan chatga
MAVZULAR = {
    "ai": "Sun'iy intellekt: barcha AI yangiliklari (AI video, rasm va musiqa ham).",
    "video": "Faqat generativ AI media: video, rasm va musiqa generatsiyasi (modellar, vositalar, yangi versiyalar va "
             "funksiyalar, narxlar, o'sish va investitsiyalar, reytinglar), shu sohalardagi tanlovlar va g'oliblar.",
}
SOHALAR = ["AI tanlov", "AI video", "AI rasm", "AI musiqa", "AI"]  # tanlov: konkurs/festival/g'oliblar, doim yuboriladi
MEDIA = ("AI video", "AI rasm", "AI musiqa")
SEEN_KEEP_DAYS = 10
# Shaxsiy blog platformalari (Bing qidiruvi ularni ham olib keladi): yangilik manbai emas
BLOG_DOMAINS = ("note.com", "medium.com", "hatenablog.com", "blogspot.com", "wordpress.com", "tistory.com",
                "naver.com", "livedoor.blog", "ameblo.jp", "dev.to", "hashnode.dev")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Asosiy model sifatliroq, lekin bepul tarifda 1-3 daqiqa javob beradi (bot fonda ishlaydi, muammo emas).
# U ishlamasa, tez lite model zaxira bo'ladi.
MODELS = [
    ("gemini-flash-latest", {"thinkingConfig": {"thinkingLevel": "low"}}, 240),
    ("gemini-flash-lite-latest", {}, 60),
]

log = logging.getLogger("bot")


def setup_logging(filename="bot.log"):
    log.setLevel(logging.INFO)
    fh = RotatingFileHandler(DIR / filename, maxBytes=500_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M"))
    log.addHandler(fh)
    if sys.stdout is not None:  # pythonw'da konsol yo'q
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        log.addHandler(logging.StreamHandler(sys.stdout))


ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GEMINI_API_KEY", "GEMINI_MODEL")


def load_env():
    """Kalitlar .env faylidan, u bo'lmasa muhit o'zgaruvchilaridan (GitHub Actions secrets)."""
    env = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def set_env_value(key, value):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("holat.json o'qilmadi, yangidan boshlanadi")
    return {"seen": {}, "fails": 0}


def save_state(state):
    cutoff = time.time() - SEEN_KEEP_DAYS * 86400
    state["seen"] = {k: t for k, t in state["seen"].items() if t > cutoff}
    state["sent"] = [x for x in state.get("sent", []) if x["t"] > time.time() - SENT_KEEP_HOURS * 3600]
    state["kunlik"] = [x for x in state.get("kunlik", []) if x["t"] > time.time() - 48 * 3600]
    if "mavzu" in state:
        topic = state["mavzu"]
        topic["seen"] = {k: t for k, t in topic["seen"].items() if t > cutoff}
        topic["sent"] = [x for x in topic["sent"] if x["t"] > time.time() - SENT_KEEP_HOURS * 3600]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def http(url, data=None, headers=None, timeout=30, retries=2):
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {"User-Agent": UA, **(headers or {})}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(20 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                time.sleep(5)
                continue
            raise


# ---------- Manbalar ----------

def read_sources():
    out = []
    for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            max_age = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            out.append((parts[0], parts[1], max_age))
    return out


def clean(text, limit):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def feed_image(e):
    """RSS yozuvidagi rasm havolasi (bo'lsa)."""
    for m in e.get("media_content", []):
        url = m.get("url", "")
        if url and (m.get("medium") == "image" or "image" in m.get("type", "")
                    or re.search(r"\.(jpe?g|png|webp)(\?|$)", url, re.I)):
            return url
    for link in e.get("links", []):
        if link.get("rel") == "enclosure" and "image" in link.get("type", ""):
            return link.get("href")
    body = (e.get("content") or [{}])[0].get("value") or e.get("summary") or ""
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", body)
    if m:
        return html.unescape(m.group(1))
    for m in e.get("media_thumbnail", []):
        if m.get("url"):
            return m["url"]
    return None


def fetch_source(src):
    name, url, max_age = (tuple(src) + (None,))[:3]
    try:
        raw = http(url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, */*"},
                   timeout=20, retries=1)
        feed = feedparser.parse(raw)
    except Exception as e:
        log.warning("Manba ishlamadi: %s (%s)", name, e)
        return []
    items = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        source = name
        if "bing.com/news/apiclick" in link:  # Bing qidiruvi: asl maqola havolasi va nashr nomi
            link = parse_qs(urlparse(link).query).get("url", [link])[0]
            source = domain(link)
        if any(domain(link).endswith(d) for d in BLOG_DOMAINS):  # shaxsiy bloglar: yangilik emas
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        items.append({
            "source": source,
            "max_age": max_age,
            "link": link,
            "title": clean(e.get("title"), 200),
            "summary": clean(e.get("summary") or e.get("description"), 300),
            "ts": calendar.timegm(ts) if ts else None,
            "image": feed_image(e),
        })
    return items


def fetch_all():
    with ThreadPoolExecutor(10) as ex:
        return [item for items in ex.map(fetch_source, read_sources()) for item in items]


def pick_candidates(fetched, exclude, max_age_hours):
    """Hali yuborilmagan, max_age_hours ichidagi yangiliklar; har manbadan eng yangilari."""
    now = time.time()
    by_source = {}
    for i in fetched:
        limit = max(max_age_hours, i.get("max_age") or 0)
        if i["link"] not in exclude and (i["ts"] is None or now - i["ts"] < limit * 3600):
            by_source.setdefault(i["source"], []).append(i)
    candidates = []
    for items in by_source.values():
        items.sort(key=lambda i: i["ts"] or now, reverse=True)
        candidates.extend(items[:PER_SOURCE_LIMIT])
    candidates.sort(key=lambda i: i["ts"] or now, reverse=True)
    return candidates[:MAX_CANDIDATES]


# ---------- Gemini ----------

PROMPT = """Sen yangiliklar muharririsan. Quyida o'quvchi qiziqadigan sohalar va yangi maqolalar ro'yxati bor.

O'QUVCHI QIZIQISHLARI:
{profile}
{mavzu}
VAZIFA:
1. Ro'yxatdan FAQAT sun'iy intellektga oid yangiliklarni tanla, boshqa hech narsa olma.
   O'quvchi AI video content maker: AI bilan video, rasm va musiqa yaratadi.
   GENERATIV MEDIA: AI VIDEO (Seedance, Kling, Veo, Sora, Runway, Hailuo, Pika, Luma, Higgsfield, Wan, Midjourney video,
   Firefly va h.k.), AI RASM (Midjourney, GPT Image, Nano Banana, Flux, Ideogram, Leonardo, Krea, Firefly va h.k.),
   AI MUSIQA (Suno, Udio, ElevenLabs music, Lyria va h.k.): yangi modellar va versiyalar, funksiyalar, narxlar,
   foydalanuvchilar va daromad o'sishi, investitsiyalar, reytinglar va arenalardagi o'rinlar, litsenziya va
   kelishuvlar, AI filmlar va kliplar - HAR BIR alohida voqeani qaytar, {media_max} tagacha.
   TANLOVLAR: biror kompaniya yoki tashkilot AI video, rasm, musiqa yoki film tanlovi, konkurs, challenge, hackathon
   yoki festival e'lon qilsa, qaysidir AI ish g'olib bo'lsa (mukofot, sovrin, reytingda birinchi o'rin), yoki
   kompaniya, tanlov yoki nashr qaysidir AI videolarni eng yaxshi/kuchli deb tanlasa (g'oliblar, finalistlar,
   "staff picks", haftaning eng yaxshi AI videolari) - bularni HAR DOIM, hammasini qaytar, baho 10.
   Qolgan AI yangiliklaridan ham eng muhim va qiziqlarini {other_max} tagacha qaytar - AI'ga oid HAR QANDAY
   yangilik bo'lishi mumkin: yangi modellar (ChatGPT, Gemini, Claude, Grok, DeepSeek, Llama va h.k.), AI kompaniyalar,
   investitsiyalar va sotib olishlar, chiplar, AI agentlar, qonunlar va siyosat, tadqiqotlar, AI bilan bog'liq
   rekordlar va janjallar. Yangi AI yangiligi bo'lsa, bo'sh qaytarma.
   Muhimlik bo'yicha tartibla, bo'sh ro'yxat qaytarma.
   Har biriga 1-10 baho qo'y.
   Bir voqea haqidagi takror maqolalardan faqat bittasini ol. "BUGUN YUBORILGAN" ro'yxatidagi voqealarni
   boshqa nashrdan bo'lsa ham qayta olma.
   Reklama, promo-kodlar, "qanday prompt yozish" qo'llanmalari, "eng yaxshi 10 ta vosita" ro'yxatlari, eski voqealarni
   eslatuvchi sharhlar va kod repozitoriylarini (GitHub) olma.
2. Har biri uchun o'zbek tilida (lotin yozuvi, o‘ g‘ va ’ belgilari bilan; maqola rus yoki ingliz tilida
   bo'lsa ham, kirill harflarisiz) yoz:
   - "sarlavha": qisqa, aniq sarlavha (12 so'zgacha), clickbait'siz.
   - "mazmun": qisqa, 1-2 gap (250 belgigacha), oddiy yangilik uslubida: nima bo'ldi, kim, asosiy raqamlar.
     O'quvchiga murojaat qilma, maslahat berma, o'zingdan tahlil va xulosa qo'shma.
   - "qidiruv": shu voqeani boshqa nashrlardan topish uchun 3-6 so'zli inglizcha qidiruv so'rovi
     (kompaniya, mahsulot va voqea nomlari, masalan "Amazon TorchNeuron Nvidia").
   - "soha": "AI tanlov" (AI video/rasm/musiqa/film tanlovi, konkurs, festival e'loni yoki g'oliblari),
     "AI video", "AI rasm", "AI musiqa" (shu generatsiyaga oid qolgan yangiliklar) yoki "AI" (qolgan AI yangiliklari).
   Brend, mahsulot va kompaniya nomlarini tarjima qilma.

Faqat shu JSON formatda javob ber:
{{"tanlov": [{{"id": 0, "baho": 8, "sarlavha": "...", "mazmun": "...", "qidiruv": "...", "soha": "..."}}]}}

BUGUN YUBORILGAN (bu voqealarni takrorlama):
{sent}

MAQOLALAR (JSON):
{items}
"""


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tanlov": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"id": {"type": "INTEGER"}, "baho": {"type": "INTEGER"},
                           "sarlavha": {"type": "STRING"}, "mazmun": {"type": "STRING"},
                           "qidiruv": {"type": "STRING"}, "soha": {"type": "STRING", "enum": SOHALAR}},
            "required": ["id", "baho", "sarlavha", "mazmun", "qidiruv", "soha"]}},
    },
    "required": ["tanlov"],
}


def call_model(env, prompt, model, extra, timeout, schema):
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json",
                             "responseSchema": schema, **extra},
    }
    started = time.time()
    for attempt, pause in enumerate((10, 25, None)):  # 503 "band" bo'lsa 3 marta urinadi
        try:
            raw = http(f"{GEMINI_BASE}/models/{model}:generateContent", payload,
                       headers={"x-goog-api-key": env["GEMINI_API_KEY"]}, timeout=timeout, retries=0)
            break
        except urllib.error.HTTPError as e:
            body = e.read()[:400]
            # Kunlik limit tugagan bo'lsa, qayta urinish befoyda: darhol keyingi modelga o'tiladi
            if e.code in (429, 500, 503) and b"PerDay" not in body and pause:
                time.sleep(pause)
                continue
            raise RuntimeError(f"{model}: HTTP {e.code} {body!r}") from None
    resp = json.loads(raw)
    parts = resp["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    result = json.loads(text)
    log.info("Gemini: %s, %.0f s", resp.get("modelVersion", model), time.time() - started)
    return result


def ask_models(env, prompt, schema, fast):
    """fast=True (tugma): avval tez lite (~5 s), u ishlamasa flash.
    fast=False (jadval, hech kim kutmaydi): avval sifatliroq flash, u ishlamasa lite."""
    models = [(env["GEMINI_MODEL"], {}, 240)] if env.get("GEMINI_MODEL") else MODELS
    if fast:
        models = models[::-1]
    last_error = None
    for model in models:
        try:
            return call_model(env, prompt, *model, schema)
        except Exception as e:
            last_error = e
            log.warning("Ishlamadi, keyingisi sinaladi: %s", e)
    raise RuntimeError(last_error)


def ask_gemini(env, candidates, sent_titles, fast=False, mavzu=None):
    items = [{"id": i, "manba": c["source"], "sarlavha": c["title"], "qisqa": c["summary"]}
             for i, c in enumerate(candidates)]
    topic = (f"\nBU SAFAR FAQAT SHU MAVZU: {MAVZULAR[mavzu]}\nBoshqa mavzudagi yangiliklarni umuman olma, "
             "sohalar aralashligi talabi bu safar yo'q. Bo'sh ro'yxat qaytarma: shu mavzudagilar kam bo'lsa, "
             "mavzuga eng yaqinlarini ol.\n") if mavzu else ""
    prompt = PROMPT.format(profile=PROFILE_FILE.read_text(encoding="utf-8").strip(), max_picks=SELECT_POOL, mavzu=topic,
                           media_max=MAX_MEDIA, other_max=MAX_OTHER + 1,
                           sent="\n".join(f"- {t}" for t in sent_titles) or "(hali yo'q)",
                           items=json.dumps(items, ensure_ascii=False))
    return ask_models(env, prompt, RESPONSE_SCHEMA, fast)


# ---------- Mutaxassislar fikri ----------
# Fikrlar faqat haqiqiy maqolalardan olinadi: asl maqola + Bing News topgan boshqa nashrlar.
# Gemini o'zidan tahlil yozmaydi; ismi manba matnida yo'q "mutaxassis" tashlab yuboriladi.

TRUSTED = ("reuters.com", "bloomberg.com", "cnbc.com", "ft.com", "wsj.com", "nytimes.com", "bbc.", "apnews.com",
           "theverge.com", "techcrunch.com", "axios.com", "fortune.com", "businessinsider.com", "wired.com",
           "arstechnica.com", "venturebeat.com", "the-decoder.com", "theguardian.com", "economist.com",
           "forbes.com", "semafor.com", "theinformation.com", "marketwatch.com", "yahoo.com", "scmp.com",
           "channelnewsasia.com", "engadget.com", "zdnet.com", "technologyreview.com", "barrons.com",
           "nikkei.com", "fastcompany.com", "theregister.com", "spot.uz", "gazeta.uz", "kun.uz", "daryo.uz")
RELATED_TEXTS = 2  # asl maqoladan tashqari nechta nashr o'qiladi
SEARCH_SITES = ("reuters.com", "cnbc.com", "bloomberg.com", "ft.com", "theverge.com", "techcrunch.com", "fortune.com",
                "businessinsider.com", "axios.com", "apnews.com", "bbc.com", "theguardian.com", "yahoo.com",
                "wired.com", "forbes.com")
EXPERT_TYPES = ["mustaqil tahlilchi", "iqtisodchi", "tadqiqotchi yoki olim", "mustaqil investor",
                "boshqa kompaniya rahbari", "soha eksperti", "kompaniya xodimi", "bitim ishtirokchisi",
                "davlat amaldori", "mijoz yoki foydalanuvchi", "boshqa"]
INDEPENDENT_TYPES = set(EXPERT_TYPES[:6])
SKIP_DOMAINS = ("msn.com", "note.com", "youtube.com", "facebook.com", "x.com", "twitter.com", "linkedin.com")

EXPERT_PROMPT = """Quyida {count} ta yangilik va har biri haqida turli nashrlardagi maqola matnlari bor.
Har bir yangilik uchun: 1) aynan shu voqea haqida mutaxassislar nima deganini shu matnlardan top;
2) bu yangilik nimalarga olib kelishi mumkinligini yoz ("oqibat").
Barcha javoblar faqat o'zbek tilida (lotin yozuvi, o‘ g‘ ’ belgilari bilan), maqola rus yoki ingliz tilida
bo'lsa ham. Kirill harflarini ishlatma.

Mutaxassis: mustaqil tahlilchi, iqtisodchi, tadqiqotchi, olim, investor, boshqa kompaniyaning taniqli rahbari
yoki soha bo'yicha tan olingan ekspert.
Mutaxassis EMAS: yangilik qilingan kompaniyaning o'z rahbarlari, xodimlari va vakillari (ularning gapi yangilikning
o'zi), bitim yoki loyihada ishtirok etayotgan investorlar, hamkorlar va mijozlar (ularning gapi reklama),
nomsiz manbalar, maqola muallifi.

Qoidalar:
- Faqat matnlarda haqiqatan yozilgan fikrlarni ol. Hech narsani o'ylab topma, o'zingdan tahlil qo'shma.
- "kompaniyalar": yangilikda ishtirok etgan barcha tashkilotlar: yangilik qilgan kompaniya, uning sho'ba
  korxonalari, bitimdagi investorlar va hamkorlar (masalan ["Amazon", "AWS"]).
- "kim": ism-familiya, matnda qanday yozilgan bo'lsa aynan shunday.
- "tashkilot": mutaxassis ishlaydigan tashkilot (matndagi nomi bilan).
- "turi": odam kimligi, ro'yxatdagi qiymatlardan biri. Yangilikdagi kompaniya xodimi -> "kompaniya xodimi";
  bitim, shartnoma yoki loyihaning tarafi (investor, hamkor, hukumat vakili) -> "bitim ishtirokchisi";
  vazir, prezident, deputat va boshqa amaldorlar -> "davlat amaldori".
- "baho": true faqat odam voqeaga baho bersa, tahlil yoki prognoz qilsa; fakt aytsa yoki o'zini maqtasa false.
- "lavozim": qisqa, o'zbek tilida (masalan "Wedbush Securities tahlilchisi").
- "fikr": 1-2 gap (220 belgigacha), o'zbek tilida (lotin, o‘ g‘ ’ belgilari bilan), mutaxassis aytgan
  mazmunni aniq yetkaz.
- "manba": fikr olingan matn raqami ("Matn N" dagi N).
- Har bir yangilik uchun ko'pi bilan 2 ta: eng obro'li mutaxassislarning eng mazmunli fikri.
  Mutaxassis fikri yo'q bo'lsa, "fikrlar" bo'sh ro'yxat.
- "oqibat": bu sening bahoying (yuqoridagi "o'zingdan qo'shma" qoidasi faqat "fikrlar"ga tegishli).
  1-2 gap (220 belgigacha): matnlardagi faktlar va mutaxassislar fikriga tayanib, bu yangilik nimalarga olib
  kelishi mumkin: bozor, raqobat, narxlar, kompaniyalar, foydalanuvchilar yoki tadbirkorlar uchun aniq oqibatlar.
  Yangilikning o'zini qayta aytib berma ("... ishga tushirdi" deb boshlama), darhol oqibatdan boshla.
  Umumiy gaplar ("katta o'zgarishlar bo'ladi") va o'quvchiga murojaat yo'q. Taxminni "...mumkin",
  "...ehtimoli bor" kabi so'zlar bilan bildir.

Faqat JSON: {{"yangiliklar": [{{"n": 1, "kompaniyalar": ["..."], "fikrlar": [{{"kim": "...", "tashkilot": "...",
"turi": "...", "baho": true, "lavozim": "...", "fikr": "...", "manba": 1}}], "oqibat": "..."}}]}}

{texts}
"""

EXPERT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"yangiliklar": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"n": {"type": "INTEGER"},
                       "kompaniyalar": {"type": "ARRAY", "items": {"type": "STRING"}},
                       "fikrlar": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"kim": {"type": "STRING"}, "tashkilot": {"type": "STRING"},
                           "turi": {"type": "STRING", "enum": EXPERT_TYPES}, "baho": {"type": "BOOLEAN"},
                           "lavozim": {"type": "STRING"}, "fikr": {"type": "STRING"},
                           "manba": {"type": "INTEGER"}},
            "required": ["kim", "tashkilot", "turi", "baho", "lavozim", "fikr", "manba"]}},
                       "oqibat": {"type": "STRING"}},
        "required": ["n", "kompaniyalar", "fikrlar", "oqibat"]}}},
    "required": ["yangiliklar"],
}


def is_uzbek_latin(text):
    letters = [ch for ch in text if ch.isalpha()]
    cyrillic = sum(1 for ch in letters if "\u0400" <= ch <= "\u04ff")
    return bool(letters) and cyrillic / len(letters) < 0.2


def domain(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def fetch_html(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in r.headers.get("Content-Type", ""):
                return None
            return r.read(800_000).decode("utf-8", "ignore")
    except Exception:
        return None


def article_text(page, limit=3500):
    if not page:
        return ""
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    paras = (clean(m, 2000) for m in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I))
    return "\n".join(t for t in paras if len(t) > 60)[:limit]


def bing_news(query):
    try:
        raw = http("https://www.bing.com/news/search?" + urlencode({"q": query, "format": "rss"}),
                   timeout=10, retries=0)
    except Exception:
        return []
    return [parse_qs(urlparse(e.get("link", "")).query).get("url", [e.get("link", "")])[0]
            for e in feedparser.parse(raw).entries]


def related_articles(query, skip_url):
    """Bing News: shu voqea haqida boshqa nashrlar. Avval obro'li nashrlar ichidan, keyin umumiy qidiruv."""
    sites = " OR ".join(f"site:{d}" for d in SEARCH_SITES)
    with ThreadPoolExecutor(2) as ex:
        trusted, general = ex.map(bing_news, (f"{query} ({sites})", query))
    urls = []
    for link in trusted + general:
        if (link.startswith("http") and domain(link) != domain(skip_url) and link not in urls
                and not any(domain(link).endswith(d) for d in SKIP_DOMAINS)):
            urls.append(link)
    urls.sort(key=lambda u: not any(t in domain(u) for t in TRUSTED))
    return urls[:8]


def gather_texts(post, related=RELATED_TEXTS):
    """[(domen, url, matn)]: asl maqola + boshqa nashrlardagi `related` tagacha maqola."""
    texts = []
    own = article_text(post["page"])
    if len(own) > 400:
        texts.append((domain(post["link"]), post["link"], own))
    urls = related_articles(post["query"], post["link"])
    with ThreadPoolExecutor(8) as ex:
        pages = list(ex.map(lambda u: fetch_html(u, timeout=6), urls))
    others = [(domain(u), u, article_text(pg)) for u, pg in zip(urls, pages)]
    texts += [t for t in others if len(t[2]) > 800][:related]
    return texts


def add_expert_views(env, posts, fast):
    """post["experts"]: topilgan fikrlar ro'yxati; None bo'lsa tekshirib bo'lmadi."""
    related = RELATED_TEXTS if len(posts) <= 4 else 1  # yangilik ko'p bo'lsa so'rov kichikroq va tezroq bo'lsin
    with ThreadPoolExecutor(len(posts)) as ex:
        all_texts = list(ex.map(lambda p: gather_texts(p, related), posts))
    blocks = []
    for n, (post, texts) in enumerate(zip(posts, all_texts), 1):
        blocks.append(f"=== YANGILIK {n}: {post['orig_title']}")
        blocks += [f"[Matn {k} - {dom}]\n{text}" for k, (dom, _, text) in enumerate(texts, 1)]
    if not any(all_texts):
        for post in posts:
            post["experts"] = []
        return
    try:
        result = ask_models(env, EXPERT_PROMPT.format(count=len(posts), texts="\n\n".join(blocks)),
                            EXPERT_SCHEMA, fast)
    except Exception as e:
        log.warning("Mutaxassislar fikri olinmadi: %s", e)
        return
    by_news = {item.get("n"): item for item in result.get("yangiliklar", [])}
    for n, (post, texts) in enumerate(zip(posts, all_texts), 1):
        item = by_news.get(n, {})
        companies = [c.lower() for c in item.get("kompaniyalar", []) if len(c) > 1]
        views = []
        for v in item.get("fikrlar", []):
            k, name = v.get("manba"), (v.get("kim") or "").strip()
            if not (isinstance(k, int) and 1 <= k <= len(texts)) or not name:
                continue
            if name.split()[-1].lower() not in texts[k - 1][2].lower():
                log.info("Matnda yo'q mutaxassis tashlandi: %s", name)
                continue
            if not is_uzbek_latin(v.get("fikr") or ""):
                log.info("O'zbekcha emas, tashlandi: %s", name)
                continue
            if v.get("turi") not in INDEPENDENT_TYPES or v.get("baho") is not True:
                log.info("Mustaqil baho emas, tashlandi: %s (%s, baho=%s)", name, v.get("turi"), v.get("baho"))
                continue
            where = f"{v.get('tashkilot', '')} {v.get('lavozim', '')}".lower()
            if any(c in where for c in companies):
                log.info("Kompaniyaning o'z odami tashlandi: %s (%s)", name, v.get("tashkilot"))
                continue
            views.append({**v, "url": texts[k - 1][1], "domain": texts[k - 1][0]})
        post["experts"] = views[:2]
        post["domains"] = list(dict.fromkeys(dom for dom, _, _ in texts))  # Instagram: 2+ manba tekshiruvi
        post["urls"] = [u for _, u, _ in texts]  # Instagram: boshqa nashrlardagi rasmlar
        effect = (item.get("oqibat") or "").strip()
        post["effect"] = effect if is_uzbek_latin(effect) else None
        log.info("Mutaxassislar: %d ta (%d ta matndan) - %s", len(post["experts"]), len(texts), post["link"])


# ---------- Telegram ----------

# Tugma har bir xabarga biriktiriladi: tugmali xabar o'chirilsa ham, keyingisi uni qaytaradi.
BUTTON = "🗞 Hozirgi yangiliklar"
VIDEO_BUTTON = "🎬 Video · Rasm · Musiqa"
KEYBOARD = {"keyboard": [[{"text": BUTTON}, {"text": VIDEO_BUTTON}]], "resize_keyboard": True, "is_persistent": True}


def tg(env, method, data=None, timeout=30, retries=2):
    url = f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/{method}"
    return json.loads(http(url, data, timeout=timeout, retries=retries))


def build_posts(result, candidates):
    posts = []
    for p in result.get("tanlov", [])[:SELECT_POOL]:
        if not (isinstance(p.get("id"), int) and 0 <= p["id"] < len(candidates)):
            continue
        c = candidates[p["id"]]
        posts.append({"title": p.get("sarlavha") or c["title"], "text": (p.get("mazmun") or "").strip()[:450],
                      "orig_title": c["title"], "query": (p.get("qidiruv") or c["title"]).strip(),
                      "link": c["link"], "source": c["source"], "soha": p.get("soha") or "boshqa",
                      "baho": p.get("baho") if isinstance(p.get("baho"), (int, float)) else 0,
                      "feed_image": c.get("image"), "page": None, "experts": None,
                      "effect": None})
    return posts


# Takror voqea filtri: Gemini "yuborilgan" ro'yxatini ko'rsa ham ba'zan bir voqeani boshqa nashrdan qayta tanlaydi
# (masalan, Suno sud ishi Tribune va The Verge'dan). Sarlavhalardagi asosiy so'zlar solishtiriladi.
GENERIC_WORDS = set("""ai artificial intelligence video videos image images music song songs model models generator
generative tool tools new launch launches launched release releases released update updates company says said
with from that this their about into after over what will more than have just now first using users user free
official officially breaking news report reports announces announced brings adds gets makes make create creates""".split())


def title_words(title):
    words = re.findall(r"[a-z0-9]+", (title or "").lower().replace("’", "'").replace("'s ", " "))
    return {w for w in words if len(w) >= 4 and w not in GENERIC_WORDS}


def same_story(a, b):
    wa, wb = title_words(a), title_words(b)
    common = len(wa & wb)
    return common >= 3 and common >= 0.5 * min(len(wa), len(wb))


def drop_repeats(posts, sent_titles):
    kept = []
    for p in posts:
        prev = sent_titles + [k["orig_title"] for k in kept]
        if any(same_story(p["orig_title"], t) for t in prev):
            log.info("Takror voqea, olinmadi: %s", p["orig_title"])
            continue
        kept.append(p)
    return kept


def visible_len(caption):
    return len(html.unescape(re.sub(r"<[^>]+>", "", caption)))


def shorten(text, limit):
    """Limitdan oshsa, iloji bo'lsa butun gap oxirida kesadi."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit * 0.5:
        return cut[:end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def make_caption(post):
    esc = html.escape
    head = f"<b>{esc(post['title'])}</b>\n\n📝 {esc(post['text'])}"
    foot = f"<a href=\"{esc(post['link'], quote=True)}\">{esc(post['source'])} →</a>"
    experts = post["experts"]

    def build(views, limit, effect_limit):
        parts = [head]
        if experts is not None:
            if views:
                lines = ["🎓 <b>Mutaxassislar fikri:</b>"]
                for v in views:
                    opinion = shorten(v["fikr"], limit)
                    lines.append(f"• <b>{esc(v['kim'])}</b> ({esc(v['lavozim'])}): {esc(opinion)} "
                                 f"<a href=\"{esc(v['url'], quote=True)}\">{esc(v['domain'])}</a>")
                parts.append("\n".join(lines))
            else:
                parts.append("🎓 Mutaxassislar fikri hali topilmadi.")
        if post["effect"] and effect_limit:
            parts.append(f"➡️ <b>Nimaga olib kelishi mumkin:</b> {esc(shorten(post['effect'], effect_limit))}")
        parts.append(foot)
        return "\n\n".join(parts)

    views = experts or []
    options = ((views, 300, 300), (views, 180, 220), (views[:1], 180, 220), (views[:1], 180, 0), ([], 0, 0))
    for option in options:  # rasm izohi limiti 1024 belgi
        caption = build(*option)
        if visible_len(caption) <= 1024:
            return caption
    return caption


def og_image(page, url):
    """Maqola sahifasidagi asosiy rasm (og:image / twitter:image)."""
    if not page:
        return None
    for pattern in (
        r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)(?::src)?[\"'][^>]*?content=[\"']([^\"']+)",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*?(?:property|name)=[\"'](?:og:image|twitter:image)[\"']",
    ):
        m = re.search(pattern, page, re.I)
        if m:
            return urljoin(url, html.unescape(m.group(1)).strip())
    return None


def download_image(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/jpeg,image/png;q=0.9,image/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=20) as r:
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype not in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
                return None
            data = r.read(10_000_001)
    except Exception:
        return None
    if len(data) > 10_000_000 or len(data) < 2_000:  # Telegram limiti / ikonka-piksel
        return None
    return data, ctype


def load_groups():
    try:
        return [g for g in json.loads(GROUPS_FILE.read_text(encoding="utf-8")) if g.get("id")]
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("guruhlar.json o'qilmadi: %s", e)
        return []


def tg_photo(env, chat_id, photo, caption, silent, keyboard):
    """photo: (bayt, turi) yoki shu rasmning Telegram'dagi file_id si (qayta yuklamaslik uchun)."""
    if isinstance(photo, str):
        payload = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML",
                   "disable_notification": silent}
        if keyboard:
            payload["reply_markup"] = KEYBOARD
        return tg(env, "sendPhoto", payload)
    data, ctype = photo
    boundary = uuid.uuid4().hex
    fields = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML",
              "disable_notification": "true" if silent else "false"}
    if keyboard:
        fields["reply_markup"] = json.dumps(KEYBOARD)
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    ext = ctype.split("/")[1].replace("jpeg", "jpg")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"rasm.{ext}\"\r\n"
             f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


VIDEO_PATTERNS = [  # maqola ichidagi video havolalari -> (toza havola, turi)
    (r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})",
     lambda m: f"https://www.youtube.com/watch?v={m.group(1)}", "YouTube"),
    (r"(?:player\.)?vimeo\.com/(?:video/)?(\d{6,})", lambda m: f"https://vimeo.com/{m.group(1)}", "Vimeo"),
    (r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status/(\d{8,})",
     lambda m: f"https://x.com/{m.group(1)}/status/{m.group(2)}", "X"),
    (r"tiktok\.com/@([A-Za-z0-9_.]+)/video/(\d{8,})",
     lambda m: f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}", "TikTok"),
    (r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]{6,})", lambda m: f"https://www.instagram.com/reel/{m.group(1)}/", "Instagram"),
]


def video_links(page, limit=8):
    """Maqola sahifasidagi videolar (YouTube, Vimeo, X, TikTok, Instagram): [(havola, turi)]."""
    found = {}
    for pattern, build, kind in VIDEO_PATTERNS:
        for m in re.finditer(pattern, page or ""):
            found.setdefault(build(m), kind)
    return list(found.items())[:limit]


def send_videos(env, chat_id, videos, keyboard):
    """Tanlov postidan keyin: videolar havolalari, birinchisining oynasi Telegram'da ochiladi."""
    lines = ["🎥 <b>Tanlovdagi videolar:</b>"]
    lines += [f"{n}. <a href=\"{html.escape(url, quote=True)}\">{kind} video</a>" for n, (url, kind) in enumerate(videos, 1)]
    payload = {"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML", "disable_notification": True,
               "link_preview_options": {"url": videos[0][0], "prefer_large_media": True}}
    if keyboard:
        payload["reply_markup"] = KEYBOARD
    tg(env, "sendMessage", payload)


def find_image(post):
    """Rasm topadi: avval maqolaning o'z rasmi, keyin RSS'dagi rasm."""
    for url in dict.fromkeys(u for u in (og_image(post["page"], post["link"]), post["feed_image"]) if u):
        img = download_image(url)
        if img:
            return url, img
    return None, None


def send_post(env, chat_id, post, silent, photo, keyboard):
    """Bitta yangilikni bitta chatga yuboradi. (tur, rasmning file_id si) qaytaradi."""
    if photo:
        try:
            r = tg_photo(env, chat_id, photo, post["caption"], silent, keyboard)
            return "rasm bilan", r["result"]["photo"][-1]["file_id"]
        except Exception as e:
            log.warning("Rasm yuborilmadi (%s), matn yuboriladi", e)
    payload = {"chat_id": chat_id, "text": post["caption"], "parse_mode": "HTML", "disable_notification": silent,
               "link_preview_options": {"url": post["link"], "prefer_large_media": True, "show_above_text": True}}
    if keyboard:
        payload["reply_markup"] = KEYBOARD
    tg(env, "sendMessage", payload)
    return "matn", None


def send(env, text):
    tg(env, "sendMessage", {
        "chat_id": env["TELEGRAM_CHAT_ID"],
        "text": text,
        "parse_mode": "HTML", "reply_markup": KEYBOARD,
        "link_preview_options": {"is_disabled": True},
    })


# ---------- Sozlash ----------

def find_chat_id(env):
    updates = tg(env, "getUpdates").get("result", [])
    chats = [u["message"]["chat"] for u in updates if "message" in u]
    if not chats:
        print("Xabar topilmadi. Telegram'da botingizga /start yozing va qayta urinib ko'ring.")
        return 1
    chat = chats[-1]
    set_env_value("TELEGRAM_CHAT_ID", str(chat["id"]))
    print(f"Chat ID saqlandi: {chat.get('first_name') or chat.get('title')} ({chat['id']})")
    return 0


def setup_wizard():
    """Kalitlarni so'raydi, tekshiradi, .env ga yozadi va chat ID ni ushlaydi."""
    import getpass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    env = load_env()
    print("\nKiritayotgan kalitingiz ekranda ko'rinmaydi, bu normal. Qo'ying (o'ng tugma yoki Ctrl+V) va Enter bosing.\n")
    while True:
        env["TELEGRAM_BOT_TOKEN"] = getpass.getpass("1) @BotFather bergan token: ").strip()
        try:
            bot_name = tg(env, "getMe")["result"]["username"]
            print(f"   ✅ Bot topildi: @{bot_name}")
            break
        except Exception:
            print("   ❌ Token noto'g'ri. @BotFather'dagi xabardan to'liq nusxa oling va qayta qo'ying.")
    old_key = env.get("GEMINI_API_KEY", "")
    question = "2) Gemini kaliti (o'zgarmasa, shunchaki Enter bosing): " if old_key else "2) Gemini kaliti (AIza...): "
    while True:
        key = getpass.getpass(question).strip() or old_key
        try:
            http(f"{GEMINI_BASE}/models?pageSize=1", headers={"x-goog-api-key": key}, retries=0)
            print("   ✅ Gemini kaliti ishlayapti")
            break
        except Exception:
            print("   ❌ Kalit ishlamadi. aistudio.google.com/apikey dan qayta nusxa oling.")
    set_env_value("TELEGRAM_BOT_TOKEN", env["TELEGRAM_BOT_TOKEN"])
    set_env_value("GEMINI_API_KEY", key)

    print(f"\n3) Endi Telegram'da @{bot_name} ni oching va /start yozing. Kutyapman...")
    seen_ids = {u["update_id"] for u in tg(env, "getUpdates").get("result", [])}
    for _ in range(100):  # ~5 daqiqa
        updates = tg(env, "getUpdates").get("result", [])
        chats = [u["message"]["chat"] for u in updates if "message" in u]
        new = [u for u in updates if u["update_id"] not in seen_ids and "message" in u]
        if new or chats:
            chat = (new or [{"message": {"chat": chats[-1]}}])[-1]["message"]["chat"]
            env["TELEGRAM_CHAT_ID"] = str(chat["id"])
            set_env_value("TELEGRAM_CHAT_ID", env["TELEGRAM_CHAT_ID"])
            send(env, "✅ Yangiliklar bot ulandi. Birinchi yangiliklar tez orada keladi.")
            print("   ✅ Ulandi! Telegram'ga tekshiruv xabari yuborildi. Bu oynani yopsangiz bo'ladi.")
            return 0
        time.sleep(3)
    print("   ⏱ /start kelmadi. Qaytadan: python bot.py --sozla")
    return 1


# ---------- Yig'ish, qulf va tugma ----------

def acquire_lock():
    """Jadval va tugma bir vaqtda yig'ib, bir yangilikni ikki marta yubormasligi uchun."""
    for _ in range(2):
        try:
            os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime < 900:
                    return False
                LOCK_FILE.unlink()  # 15 daqiqadan eski qulf: jarayon uzilib qolgan
            except FileNotFoundError:
                pass
    return False


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


def choose_posts(posts, only_video=False):
    """Yuboriladiganlar: tanlovlar va g'oliblar (hammasi, birinchi) + AI video/rasm/musiqa yangiliklari
    (MAX_MEDIA tagacha) + qolgan AI'dan MAX_OTHER tasi. Qolgan tartib Gemini'niki (muhimlik).
    only_video: "🎬 Video · Rasm · Musiqa" tugmasi, faqat generativ media."""
    contests = [p for p in posts if p["soha"] == "AI tanlov"]
    media = [p for p in posts if p["soha"] in MEDIA][:MAX_MEDIA]
    other = [] if only_video else [p for p in posts if p["soha"] not in MEDIA + ("AI tanlov",)][:MAX_OTHER]
    return contests + [p for p in posts if p in media or p in other]


def run_digest(env, dry=False, fast=False, extra_chats=(), mavzu=None):
    """Yangi yangiliklarni yig'ib yuboradi. Yuborilganlar sonini, xato bo'lsa None qaytaradi.
    mavzu ("ai"/"ilm") bo'lsa: faqat shu mavzu, faqat so'ragan chatga; umumiy lentaga ta'sir qilmaydi."""
    state = load_state()
    topic = state.setdefault("mavzu", {"seen": {}, "sent": []}) if mavzu else None
    fetched = fetch_all()
    exclude = set(state["seen"]) | (set(topic["seen"]) if topic else set())
    candidates = pick_candidates(fetched, exclude, MAX_AGE_HOURS)
    if not candidates:  # bugungilarning hammasi yuborilgan bo'lsa, kechagilar ham olinadi
        candidates = pick_candidates(fetched, exclude, FALLBACK_AGE_HOURS)
    log.info("Yig'ildi: %d ta, yuborilmagan nomzod: %d ta", len(fetched), len(candidates))
    if not candidates:
        return 0
    now = time.time()
    sent_list = state.get("sent", []) + (topic["sent"] if topic else [])
    sent_titles = [x["title"] for x in sent_list if now - x["t"] < 86400][-40:]

    try:
        result = ask_gemini(env, candidates, sent_titles, fast=fast, mavzu=mavzu)
    except Exception as e:
        state["fails"] = state.get("fails", 0) + 1
        log.error("Gemini xatosi (%d-marta): %s", state["fails"], e)
        if state["fails"] == 3 and not dry:
            send(env, f"⚠️ Yangiliklar bot: Gemini 3 marta ketma-ket ishlamadi.\n<code>{html.escape(str(e)[:300])}</code>")
        if not dry:
            save_state(state)
        return None
    state["fails"] = 0

    posts = build_posts(result, candidates)
    log.info("Tanlandi: %d ta", len(posts))
    posts = drop_repeats(posts, [x["title"] for x in sent_list])  # yuborilganlarning hammasi (SENT_KEEP_HOURS)
    posts = choose_posts(posts, only_video=mavzu == "video")
    log.info("Yuboriladi: %d ta (tanlov: %d, video/rasm/musiqa: %d)", len(posts),
             sum(p["soha"] == "AI tanlov" for p in posts), sum(p["soha"] in MEDIA for p in posts))
    if posts:  # mutaxassislar fikri faqat yuboriladiganlar uchun qidiriladi
        with ThreadPoolExecutor(len(posts)) as ex:
            for post, page in zip(posts, ex.map(lambda p: fetch_html(p["link"]), posts)):
                post["page"] = page
        add_expert_views(env, posts, fast)
        for post in posts:
            post["caption"] = make_caption(post)
            post["videos"] = video_links(post["page"]) if post["soha"] == "AI tanlov" else []
    with ThreadPoolExecutor(max(len(posts), 1)) as ex:
        images = list(ex.map(find_image, posts))
    if dry:
        for post, (url, _) in zip(posts, images):
            print(f"{post['caption']}\n[rasm: {url or 'yo‘q'}]\n")
        return len(posts)
    owner = str(env["TELEGRAM_CHAT_ID"])
    if mavzu:  # mavzu bo'yicha so'rov: faqat so'ragan chatga
        targets = {str(c): str(c) for c in (extra_chats or [owner])}
    else:  # umumiy lenta: egasi + ro'yxatdagi guruhlar + buyruq kelgan, hali ro'yxatda yo'q guruh
        targets = {owner: "egasi"}
        targets.update({str(g["id"]): g.get("nomi") or str(g["id"]) for g in load_groups()})
        targets.update({str(c): str(c) for c in extra_chats if str(c) not in targets})
    for n, (post, (_, img)) in enumerate(zip(posts, images)):  # faqat birinchisi ovoz bilan keladi
        photo, kind = img, "yuborilmadi"
        for chat_id, name in targets.items():  # tugma faqat shaxsiy chatlarda (ID musbat), guruhlarda yo'q
            try:
                kind, file_id = send_post(env, chat_id, post, n > 0, photo, keyboard=not chat_id.startswith("-"))
                photo = file_id or photo  # keyingi chatlarga yuklangan rasm qayta ishlatiladi
                if post.get("videos"):
                    send_videos(env, chat_id, post["videos"], keyboard=not chat_id.startswith("-"))
            except Exception as e:
                log.warning("Yuborilmadi: %s (%s)", name, e)
        log.info("Yuborildi (%s): %s", kind, post["link"])
    if posts:
        log.info("Qayerga: %s%s", ", ".join(targets.values()), f" (mavzu: {mavzu})" if mavzu else "")
    now = time.time()
    record = topic if mavzu else state
    for post in posts:  # faqat yuborilganlar belgilanadi: qolganlari keyingi safar yana nomzod
        record["seen"][post["link"]] = now
        record.setdefault("sent", []).append({"t": now, "title": post["orig_title"], "soha": post["soha"]})
    if not mavzu:  # Instagram kunlik posti uchun to'liq ma'lumot (instagram/kunlik.py o'qiydi)
        for post, (img_url, _) in zip(posts, images):
            state.setdefault("kunlik", []).append({
                "t": now, "title": post["title"], "text": post["text"], "orig_title": post["orig_title"],
                "link": post["link"], "source": post["source"], "soha": post["soha"], "baho": post["baho"],
                "experts": [{k: v.get(k) for k in ("kim", "lavozim", "fikr", "url", "domain")}
                            for v in (post["experts"] or [])],
                "effect": post["effect"], "image": img_url, "domains": post.get("domains") or [],
                "urls": post.get("urls") or []})
    save_state(state)
    return len(posts)


def finish_wait(env, wait_id, sent, chat=None):
    """⏳ xabarini yangiliklar kelgach o'chiradi, kelmasa sababini yozadi."""
    if not wait_id:
        return
    chat = chat or env["TELEGRAM_CHAT_ID"]
    if sent:
        tg(env, "deleteMessage", {"chat_id": chat, "message_id": wait_id})
        return
    note = ("Manbalardan yangilik olinmadi, birozdan keyin qayta bosing." if sent == 0
            else "⚠️ Xatolik bo'ldi, keyinroq qayta urinib ko'ring.")
    tg(env, "editMessageText", {"chat_id": chat, "message_id": wait_id, "text": note})


def on_demand(env):
    chat = env["TELEGRAM_CHAT_ID"]
    wait_id = tg(env, "sendMessage", {"chat_id": chat, "text": "⏳ Yangiliklar yig'ilyapti..."}
                 )["result"]["message_id"]
    if not acquire_lock():
        tg(env, "editMessageText", {"chat_id": chat, "message_id": wait_id,
                                    "text": "⏳ Yangiliklar hozir tayyorlanyapti, birozdan keyin o'zi keladi."})
        return
    try:
        sent = run_digest(env, fast=True)
    finally:
        release_lock()
    finish_wait(env, wait_id, sent)


def listen(env):
    """Telegram'dagi tugmani kutadi va bosilganda yangiliklarni darhol yuboradi."""
    log.info("Tugma kutilmoqda")
    try:
        tg(env, "setMyCommands", {"commands": [
            {"command": "yangiliklar", "description": "Hozirgi yangiliklarni ko'rish"}]})
    except Exception as e:
        log.warning("Buyruqlar menyusi o'rnatilmadi: %s", e)
    offset = 0
    while True:
        try:
            updates = tg(env, "getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message"]},
                         timeout=70, retries=0)["result"]
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != env["TELEGRAM_CHAT_ID"]:
                    continue  # boshqalarga javob bermaydi
                text = (msg.get("text") or "").strip()
                if text == BUTTON or text.startswith("/yangiliklar"):
                    on_demand(env)
                elif text.startswith("/start"):
                    tg(env, "sendMessage", {
                        "chat_id": env["TELEGRAM_CHAT_ID"], "reply_markup": KEYBOARD,
                        "text": "AI yangiliklari har 2 soatda keladi. Oldinroq ko'rmoqchi bo'lsangiz, "
                                "pastdagi «" + BUTTON + "» tugmasini bosing."})
        except Exception as e:
            log.warning("Tinglashda xato: %s", e)
            time.sleep(15)


# ---------- Asosiy ----------

def main():
    if "--sozla" in sys.argv:
        return setup_wizard()
    listening = "--tinglash" in sys.argv
    setup_logging("tugma.log" if listening else "bot.log")
    env = load_env()
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY") if not env.get(k)]
    if missing:
        log.error(".env faylida yo'q: %s", ", ".join(missing))
        return 1
    if "--chat-id" in sys.argv:
        return find_chat_id(env)
    dry = "--dry" in sys.argv
    if not dry and not env.get("TELEGRAM_CHAT_ID"):
        log.error("TELEGRAM_CHAT_ID yo'q. Avval: python bot.py --chat-id")
        return 1
    if listening:
        return listen(env)
    if "--tugma" in sys.argv:  # bulut (GitHub Actions): bir vaqtda bitta ish, qulf kerak emas
        arg = sys.argv[sys.argv.index("--tugma") + 1:] + ["", "", ""]
        wait_id = int(arg[0]) if arg[0].isdigit() else None
        wait_chat = arg[1] if arg[1].lstrip("-").isdigit() else None
        mavzu = arg[2] if arg[2] in MAVZULAR else None
        sent = run_digest(env, fast=True, extra_chats=[wait_chat] if wait_chat else (), mavzu=mavzu)
        finish_wait(env, wait_id, sent, wait_chat)
        return 0 if sent is not None else 1
    if dry:
        return 0 if run_digest(env, dry=True) is not None else 1
    if not acquire_lock():
        log.info("Boshqa yig'ish ishlayapti, bu safar o'tkazildi")
        return 0
    try:
        # Har soatda ishlaydi: Gemini flash bepul tarifda kuniga ~20 so'rov (har run 2 ta), shuning uchun
        # toq UTC soatlarda flash, juft soatlarda tez lite model birinchi.
        sent = run_digest(env, fast=time.gmtime().tm_hour % 2 == 0)
    finally:
        release_lock()
    return 0 if sent is not None else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("Kutilmagan xato")
        sys.exit(1)
