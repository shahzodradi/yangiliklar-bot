# AI Video Yangiliklar (content maker uchun)

Faqat generativ AI media: video (Seedance, Kling, Veo, Sora, Runway...), rasm (Midjourney, GPT Image, Nano Banana,
Flux...) va musiqa (Suno, Udio, Lyria...) generatsiyasidagi har bir yangilik, o'sish, reyting, shu sohalardagi
tanlov, festival va g'oliblar. Har safar: tanlovlar (hammasi) + video/rasm/musiqa (8 tagacha) + umumiy AI'dan
content maker uchun foydali 1 ta. 56 ta manba (AI saytlari + Bing News qidiruvlari: media 72 soatlik, tanlovlar 7 kunlik).
Gemini eng muhimlarini tanlab, o'zbekchaga qisqa qilib yozadi va Telegram'ga yuboradi (@yangiliklaraivabiznes_bot).
Har yangilik: rasm, qisqa mazmun, mustaqil mutaxassislar fikri va "nimaga olib kelishi mumkin".
Mutaxassislar fikri haqiqiy maqolalardan olinadi (asl maqola + Bing News topgan Reuters, Yahoo Finance va h.k.),
har bir fikr yonida manba havolasi bor. Kompaniyaning o'z odamlari, bitim ishtirokchilari va amaldorlar hisobga olinmaydi.

## Qayerda ishlaydi (kompyuter o'chiq bo'lsa ham)
- **GitHub Actions** (`.github/workflows/yangiliklar.yml`, repo: shahzodradi/yangiliklar-bot, private):
  Toshkent vaqti bilan har 2 soatda (08:00–24:00): AI video tanlovlari va g'oliblari (hammasi), AI video yangiliklari
  (6 tagacha) va umumiy AI'dan 2 ta.
  `holat.json` (yuborilganlar) har ishdan keyin repo'ga saqlanadi. Kalitlar: GitHub → Settings → Secrets.
- **Cloudflare Worker** (`cloudflare-worker/`, yangiliklar-tugma.shahzodabdurahmanov0.workers.dev):
  Telegram'dagi **🗞 Hozirgi yangiliklar** tugmasi yoki `/yangiliklar` bosilganda GitHub'dagi botni ishga tushiradi,
  yangiliklar 1-2 daqiqada keladi. Kalitlar: `wrangler secret put ...`.
- **Guruhlar:** botni Telegram guruhiga (yoki kanalga admin qilib) qo'shsangiz, yangiliklar u yerga ham boradi.
  Worker guruhni `guruhlar.json` ga qo'shadi (`.github/workflows/guruh.yml` orqali), botni chiqarsangiz o'chiradi.
  Faqat siz qo'shgan guruhlar hisobga olinadi: begona qo'shsa, bot o'sha guruhdan chiqib ketadi.
  Guruhda `/yangiliklar@yangiliklaraivabiznes_bot` buyrug'ini egasi yoki guruh admini bera oladi (guruhni ro'yxatga ham qo'shadi).
  Egasining boshqa Telegram akkauntlari: Worker secret `OWNER_IDS`.
- **Buyruqlar** (shaxsiy chatda tugmalar, guruhda buyruqlar; egasi yoki guruh admini):
  **🗞 Hozirgi yangiliklar** / `/yangiliklar`: AI yangiliklari hamma chatlarga. **🎬 Video · Rasm · Musiqa** / `/video`: faqat generativ media,
  faqat so'ragan chatga (`/ai` ham bor: hamma AI, faqat so'ragan chatga). Mavzu so'rovlari umumiy lentaga ta'sir qilmaydi.
- Kompyuterdagi eski Task Scheduler vazifalari ("Yangiliklar bot", "Yangiliklar bot tugma") o'chirib qo'yilgan.

## Fayllar
- `profil.txt`: nimalar qiziq va nimalar qiziq emas. Tahrirlab, `git push` qilsangiz, saralash o'zgaradi.
- `manbalar.txt`: RSS manbalar. Qator qo'shing yoki boshiga `#` qo'yib o'chiring.
- `.env`: mahalliy kalitlar (GitHub'ga yuklanmaydi, hech kimga bermang).
- `holat.json`: faqat haqiqatan yuborilgan yangiliklar (havola + sarlavha), takror kelmasligi uchun.

## Buyruqlar
    gh workflow run yangiliklar.yml        # hozir bulutda ishga tushirish
    gh run list --workflow yangiliklar.yml # oxirgi ishlar
    python bot.py --dry                    # kompyuterda, yubormasdan ko'rish
    cd cloudflare-worker && wrangler deploy  # tugma kodini yangilash

Gemini: jadvalda `gemini-flash-latest` (bepul tarifda kuniga 20 so'rov, sekin), tugmada va zaxira sifatida `gemini-flash-lite-latest`.
