"""AI.TezkorYangiliklari — R3 (airesearches) uslubida IG post: rasm + izoh -> @malikalardekqizlargabot.
Qo'lda: python post.py posts/2026-11-10.json [--yubor]   (kunlik avtomatik: kunlik.py)"""
import json, os, sys, pathlib, platform, shutil, subprocess, urllib.request, uuid

HERE = pathlib.Path(__file__).parent
OUT = HERE / "chiqish"
HANDLE = "AI.TezkorYangiliklari"
FOOT = "Batafsil izohlarda oʻqing 👇"

HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@800&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1350px;overflow:hidden;background:#000}
.ph{height:870px;background:url('%(img)s') %(pos)s/%(fit)s no-repeat,radial-gradient(circle at 50%% 45%%,#0d3b8f,#020814 75%%)}
.p{height:480px;background:#000;display:flex;flex-direction:column;align-items:center;padding:0 40px}
.sm{font-family:Inter,sans-serif;font-weight:800;letter-spacing:2px;text-transform:uppercase}
.logo{font-size:22px;color:#fff;margin-top:-16px;background:#000;padding:4px 16px}
h1{font-family:Anton,Impact,sans-serif;font-weight:400;text-transform:uppercase;font-size:%(fs)spx;line-height:1.06;
 color:#fff;text-align:center;margin-top:30px;letter-spacing:.5px}
h1 b{color:#3d8bff;font-weight:400;white-space:nowrap}
.sw{font-size:24px;color:#cfcfcf;margin-top:auto;margin-bottom:40px}
</style></head><body><div class="ph"></div><div class="p"><div class="logo sm">%(handle)s</div>
<h1>%(h1)s</h1><div class="sw sm">%(foot)s</div></div></body></html>"""


def font_size(h1_text):
    """Sarlavha uzun bo'lsa shrift kichrayadi (panelga 3-4 qator sig'sin)."""
    n = len(h1_text.replace("<b>", "").replace("</b>", ""))
    return 90 if n <= 70 else 80 if n <= 95 else 70


LOGO = HERE / "logo" / "L1_evolving.png"


def render(name, image_path, h1, pos="center 15%"):
    """image_path=None: sifatli rasm topilmagan yangilik — xira rasm o'rniga ko'k fonda logo kartasi."""
    OUT.mkdir(exist_ok=True)
    html = OUT / f"{name}.html"
    fit = "cover"
    if image_path is None:
        image_path, pos, fit = LOGO, "center", "520px"
    html.write_text(HTML % dict(img=pathlib.Path(image_path).resolve().as_uri(), pos=pos, fit=fit, fs=font_size(h1),
                                h1=h1, handle=HANDLE, foot=FOOT), encoding="utf-8")
    png = OUT / f"{name}.png"
    png.unlink(missing_ok=True)
    profile = OUT / f"_edge_{uuid.uuid4().hex[:6]}"
    flags = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox", f"--user-data-dir={profile}",
             "--allow-file-access-from-files", "--virtual-time-budget=6000", "--window-size=1080,1350",
             f"--screenshot={png}", html.resolve().as_uri()]
    if platform.system() == "Windows":
        # Bash/Python'dan to'g'ridan-to'g'ri ishga tushirilgan Edge PNG yozmaydi -> PowerShell Start-Process -Wait
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        args = ",".join(f"'{a}'" for a in flags)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process -FilePath '{edge}' -ArgumentList {args} -Wait"], timeout=120)
    else:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or "chromium-browser"
        subprocess.run([chrome, *flags], timeout=120, capture_output=True)
    shutil.rmtree(profile, ignore_errors=True)
    return png if png.exists() else None


def ig_bot():
    """(token, chat): GitHub'da IG_BOT_TOKEN/IG_CHAT_ID, kompyuterda malikalardek-bot/.env."""
    if os.environ.get("IG_BOT_TOKEN"):
        return os.environ["IG_BOT_TOKEN"], os.environ["IG_CHAT_ID"]
    d = {}
    for line in (HERE.parent.parent / "malikalardek-bot" / ".env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1); d[k.strip()] = v.strip()
    return d["BOT_TOKEN"], d["OWNER_ID"]


def tg(token, method, fields, files=None):
    b = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, path in (files or {}).items():
        body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{path.name}"\r\n'
                 f"Content-Type: image/png\r\n\r\n").encode() + path.read_bytes() + b"\r\n"
    body += f"--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", body,
                                 {"Content-Type": f"multipart/form-data; boundary={b}"})
    return json.load(urllib.request.urlopen(req, timeout=60))["ok"]


WORKER = "https://malikalardek-bot.shahzodabdurahmanov0.workers.dev"


def upload_secret():
    if os.environ.get("IG_BOT_TOKEN"):  # GitHub'da: Worker bot tokenini ham yuklash paroli sifatida qabul qiladi
        return os.environ["IG_BOT_TOKEN"]
    for line in (HERE.parent.parent / "malikalardek-bot" / ".env").read_text().splitlines():
        if line.startswith("WEBHOOK_SECRET="):
            return line.split("=", 1)[1].strip()


def upload_for_instagram(png, caption, publish=False):
    """PNG -> JPEG (Instagram faqat JPEG oladi), Worker KV ga yuklaydi. Post ID sini qaytaradi yoki None.
    publish=True: Worker darhol Instagram'ga joylaydi -> (pid, javob dict)."""
    import base64, io
    from PIL import Image
    buf = io.BytesIO()
    Image.open(png).convert("RGB").save(buf, "JPEG", quality=93, optimize=True)
    pid = f"{png.stem}_{uuid.uuid4().hex[:6]}"
    body = json.dumps({"id": pid, "jpg": base64.b64encode(buf.getvalue()).decode(), "caption": caption,
                       "publish": publish}).encode()
    req = urllib.request.Request(f"{WORKER}/ig/upload", body, {"Content-Type": "application/json",
                                 "X-Upload-Secret": upload_secret(), "User-Agent": "yangiliklar-bot"})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=120))
    except Exception as e:
        print("Instagram uchun yuklanmadi:", e)
        return (None, {"error": str(e)}) if publish else None
    if publish:
        return (pid if res.get("ok") else None), res
    return pid if res.get("ok") else None


def publish_post(token, chat, png, caption):
    """Avtomatik rejim: Instagram'ga darhol joylaydi, botga rasm + natijani yuboradi. True = joylandi."""
    pid, res = upload_for_instagram(png, caption, publish=True)
    ok = bool(pid and res.get("published"))
    status = "✅ Instagram'ga joylandi" if ok else f"⚠️ Instagram'ga joylanmadi: {res.get('error', '?')}"
    tg(token, "sendDocument", {"chat_id": chat, "caption": status[:1000]}, {"document": png})
    if not ok and pid:  # xato bo'lsa qo'lda joylash tugmasi qoladi
        tg(token, "sendMessage", {"chat_id": chat, "text": "📲 Qayta urinib koʻraymi?",
                                  "reply_markup": json.dumps({"inline_keyboard": [[
                                      {"text": "✅ Instagram'ga joylash", "callback_data": f"ig:{pid}"},
                                      {"text": "❌ Yoʻq", "callback_data": f"igx:{pid}"}]]})})
    return ok


def send_post(token, chat, n, png, caption):
    """Rasm (fayl) + izoh + "Instagram'ga joylash" tugmasi (bosilganda Worker Instagram API orqali joylaydi)."""
    ok1 = tg(token, "sendDocument", {"chat_id": chat, "caption": f"📸 Post {n} — rasm (1080x1350)"}, {"document": png})
    ok2 = tg(token, "sendMessage", {"chat_id": chat, "text": caption})
    pid = upload_for_instagram(png, caption)
    if pid:
        tg(token, "sendMessage", {"chat_id": chat, "text": f"📲 Post {n}: Instagram'ga joylaymi?",
                                  "reply_markup": json.dumps({"inline_keyboard": [[
                                      {"text": "✅ Instagram'ga joylash", "callback_data": f"ig:{pid}"},
                                      {"text": "❌ Yoʻq", "callback_data": f"igx:{pid}"}]]})})
    return ok1 and ok2


def main():
    src = pathlib.Path(sys.argv[1])
    posts = json.loads(src.read_text(encoding="utf-8"))
    send = "--yubor" in sys.argv
    token, chat = ig_bot() if send else (None, None)
    for i, p in enumerate(posts, 1):
        png = render(f"{src.stem}_{i}", HERE / p["rasm"], p["sarlavha"], p.get("pos", "center 0"))
        print(i, "png" if png else "PNG XATO")
        if send and png:
            print("  yuborildi:", send_post(token, chat, i, png, p["izoh"]))


if __name__ == "__main__":
    main()
