# Yangiliklar bot

AI, texnologiya, biznes, ijtimoiy tarmoqlar va o'sish yangiliklarini 26 ta manbadan yig'adi.
Gemini sizga mosini saralab, o'zbekchaga qisqa qilib yozadi va Telegram'ga yuboradi.
Har kuni 08, 11, 14, 17, 20, 23 da bugungi yangiliklardan eng muhim 3 tasini yuboradi: rasm, qisqa mazmun, mustaqil mutaxassislar fikri
va "nimaga olib kelishi mumkin". Yuborilmagan yangiliklar keyingi safar yana nomzod bo'ladi, "yangilik yo'q" deb kelmaydi.
Mutaxassislar fikri haqiqiy maqolalardan olinadi (asl maqola + Bing News topgan Reuters, Yahoo Finance va h.k.),
har bir fikr yonida manba havolasi bor. Kompaniyaning o'z odamlari, bitim ishtirokchilari va amaldorlar hisobga olinmaydi.

Oldinroq ko'rmoqchi bo'lsangiz: Telegram'da pastdagi **🗞 Hozirgi yangiliklar** tugmasini bosing yoki `/yangiliklar` yozing.
Bot oxirgi yuborilgandan beri chiqqan yangiliklarni 1-3 daqiqada yuboradi. Buning uchun kompyuter yoqiq bo'lishi kerak.

## Fayllar
- `profil.txt`: nimalar qiziq va nimalar qiziq emas. Shu yerni tahrirlasangiz, saralash o'zgaradi.
- `manbalar.txt`: RSS manbalar. Qator qo'shing yoki boshiga `#` qo'yib o'chiring.
- `.env`: kalitlar (hech kimga bermang).
- `bot.log`: jadval bo'yicha ishlar jurnali; `tugma.log`: tugma jurnali.
- `holat.json`: faqat haqiqatan yuborilgan yangiliklar (havola + sarlavha), takror kelmasligi uchun.

## Buyruqlar
    python bot.py --sozla    # token/kalitni qayta kiritish (tekshirib saqlaydi)
    python bot.py --dry      # yubormasdan ko'rish
    python bot.py            # hozir yuborish
    powershell -ExecutionPolicy Bypass -File vazifa_ornatish.ps1   # jadvalga qo'yish

Gemini: asosiy model `gemini-flash-latest` (bepul tarifda 30 soniyadan 3 daqiqagacha javob beradi), u ishlamasa `gemini-flash-lite-latest`.
Bot: @yangiliklaraivabiznes_bot.

Task Scheduler'da 2 ta vazifa bor: "Yangiliklar bot" (har 3 soat) va "Yangiliklar bot tugma" (tugmani kutadi).
O'chirish: ikkalasini ham Task Scheduler'da tanlab → Delete.
