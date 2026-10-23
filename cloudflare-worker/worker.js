// Telegram'dagi "🗞 Hozirgi yangiliklar" tugmasi uchun ko'prik (Cloudflare Worker, bepul).
// Telegram botga kelgan har bir xabarni shu yerga yuboradi (webhook). Tugma bosilsa ⏳ xabarini
// yuborib, GitHub'dagi "Yangiliklar" workflow'ini ishga tushiradi. Yangiliklarni GitHub'dagi bot.py yuboradi.
//
// Bot guruhga qo'shilsa yoki chiqarilsa, guruh.yml orqali guruhlar.json ro'yxatini yangilaydi
// (yangiliklar shu guruhlarga ham boradi). Faqat egasi qo'shgan guruhlar hisobga olinadi.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GITHUB_TOKEN, WEBHOOK_SECRET,
// OWNER_IDS (egasining boshqa Telegram akkauntlari, vergul bilan).
// GITHUB_TOKEN: fine-grained, faqat shu repo, Actions: Read and write.

const BUTTON = '🗞 Hozirgi yangiliklar';
const AI_BUTTON = '🤖 AI yangiliklari';
const ILM_BUTTON = '🔬 Ilm va yutuqlar';
const KEYBOARD = {keyboard: [[{text: BUTTON}], [{text: AI_BUTTON}, {text: ILM_BUTTON}]],
                  resize_keyboard: true, is_persistent: true};
// Buyruq yoki tugma -> mavzu ('' = umumiy yangiliklar, hamma chatlarga; ai/ilm = faqat so'ragan chatga)
const COMMANDS = {[BUTTON]: '', '/yangiliklar': '', [AI_BUTTON]: 'ai', '/ai': 'ai', [ILM_BUTTON]: 'ilm', '/ilm': 'ilm'};
const WAIT_TEXT = {'': "⏳ Yangiliklar yig'ilyapti, 1-2 daqiqa...", ai: "⏳ AI yangiliklari yig'ilyapti, 1-2 daqiqa...",
                   ilm: "⏳ Ilm-fan va yutuqlar yig'ilyapti, 1-2 daqiqa..."};
const WORKFLOW = 'yangiliklar.yml';

export default {
  async fetch(request, env, ctx) {
    // Faqat Telegram (setWebhook'dagi secret_token bilan) kira oladi
    if (request.method !== 'POST' ||
        request.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.WEBHOOK_SECRET) {
      return new Response('not found', {status: 404});
    }
    const update = await request.json().catch(() => ({}));
    ctx.waitUntil(handle(update, env));
    return new Response('ok');  // Telegram'ga darhol javob: aks holda u xabarni qayta yuboradi
  },
};

async function tg(env, method, payload) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  });
  return res.json();
}

function github(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json',
              'User-Agent': 'yangiliklar-tugma', 'Content-Type': 'application/json'},
  });
}

async function handle(update, env) {
  if (update.my_chat_member) return handleMembership(update.my_chat_member, env);
  const msg = update.message || {};
  if (msg.migrate_to_chat_id) {  // guruh supergruppaga aylandi: ID o'zgaradi
    await dispatchGroup(env, 'olib', msg.chat.id, '');
    await dispatchGroup(env, 'qosh', msg.migrate_to_chat_id, msg.chat.title || '');
    return;
  }
  const chat = msg.chat || {};
  const inGroup = chat.type !== 'private';
  const text = (msg.text || '').trim();
  const command = text.startsWith('/') ? text.split(/[\s@]/)[0].toLowerCase() : text;  // "/ai@bot" -> "/ai"
  if (!(command in COMMANDS) && command !== '/start') return;  // oddiy xabarlar bilan ishi yo'q
  if (!(await allowed(env, msg))) return;  // faqat egasi yoki shu guruhning admini
  if (command in COMMANDS) {
    if (inGroup) await dispatchGroup(env, 'qosh', chat.id, chat.title || '');  // ro'yxatda bo'lmasa qo'shiladi
    await startRun(env, chat.id, COMMANDS[command]);
  } else if (command === '/start') {
    if (inGroup) {
      await dispatchGroup(env, 'qosh', chat.id, chat.title || '');
      await tg(env, 'sendMessage', {chat_id: chat.id,
        text: "✅ Yangiliklar shu guruhga ham keladi: har kuni 08:00–23:00 oralig'ida, har 3 soatda."});
    } else {
      await tg(env, 'sendMessage', {chat_id: chat.id, reply_markup: KEYBOARD,
        text: `Yangiliklar har 3 soatda keladi. Oldinroq ko'rmoqchi bo'lsangiz, pastdagi «${BUTTON}» tugmasini bosing.`});
    }
  }
}

function isOwner(env, userId) {
  const owners = [env.TELEGRAM_CHAT_ID, ...String(env.OWNER_IDS || '').split(',')].map((x) => String(x).trim());
  return owners.includes(String(userId));
}

async function allowed(env, msg) {
  const chat = msg.chat || {};
  if (isOwner(env, (msg.from || {}).id)) return true;  // egasi (istalgan akkaunti)
  if (chat.type === 'private') return false;
  if (msg.sender_chat && msg.sender_chat.id === chat.id) return true;  // anonim admin (guruh nomidan)
  const member = await tg(env, 'getChatMember', {chat_id: chat.id, user_id: msg.from.id});
  const ok = member.ok && ['creator', 'administrator'].includes(member.result.status);
  return ok;
}

const INSIDE = ['member', 'administrator', 'creator', 'restricted'];

async function handleMembership(m, env) {
  const chat = m.chat || {};
  if (chat.type === 'private') return;
  const name = chat.title || String(chat.id);
  const was = INSIDE.includes(m.old_chat_member.status);
  const now = INSIDE.includes(m.new_chat_member.status);
  if (!was && now) {
    if (!isOwner(env, m.from.id)) {  // begona qo'shgan bo'lsa, chiqib ketadi
      await tg(env, 'sendMessage', {chat_id: chat.id, text: "Bu bot faqat egasi qo'shgan guruhlarda ishlaydi."});
      await tg(env, 'leaveChat', {chat_id: chat.id});
      return;
    }
    await tg(env, 'sendMessage', {chat_id: chat.id,
      text: "✅ Yangiliklar endi shu guruhga ham keladi: har kuni 08:00–23:00 oralig'ida, har 3 soatda."});
    await tg(env, 'sendMessage', {chat_id: env.TELEGRAM_CHAT_ID, text: `✅ «${name}» guruhi ro'yxatga qo'shildi.`});
    await dispatchGroup(env, 'qosh', chat.id, chat.title || '');
  } else if (was && !now) {
    await dispatchGroup(env, 'olib', chat.id, chat.title || '');
    await tg(env, 'sendMessage', {chat_id: env.TELEGRAM_CHAT_ID,
      text: `«${name}» guruhidan chiqarildi, u yerga endi yangilik yuborilmaydi.`});
  }
}

function dispatchGroup(env, amal, chatId, nomi) {
  return github(env, '/actions/workflows/guruh.yml/dispatches', {
    method: 'POST', body: JSON.stringify({ref: 'main', inputs: {amal, chat_id: String(chatId), nomi}}),
  });
}

async function startRun(env, chatId, mavzu = '') {
  // Tugma ketma-ket bosilsa, navbatdagi yoki ishlayotgan ish bo'lsa, yangisi boshlanmaydi
  const runs = await github(env, `/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=5`)
    .then((r) => r.json()).catch(() => ({}));
  const busy = (runs.workflow_runs || []).some((r) => ['queued', 'in_progress', 'waiting', 'pending'].includes(r.status));
  if (busy) {
    await tg(env, 'sendMessage', {chat_id: chatId, text: '⏳ Yangiliklar allaqachon tayyorlanyapti, birozdan keyin keladi.'});
    return;
  }
  const wait = await tg(env, 'sendMessage', {chat_id: chatId, text: WAIT_TEXT[mavzu]});
  const waitId = wait.ok ? String(wait.result.message_id) : '';
  const res = await github(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: 'POST', body: JSON.stringify({ref: 'main', inputs: {wait_id: waitId, wait_chat: String(chatId), mavzu}}),
  });
  if (res.status !== 204 && waitId) {
    await tg(env, 'editMessageText', {chat_id: chatId, message_id: Number(waitId),
      text: `⚠️ Bot ishga tushmadi (GitHub javobi ${res.status}).`});
  }
}
