// Telegram'dagi "🗞 Hozirgi yangiliklar" tugmasi uchun ko'prik (Cloudflare Worker, bepul).
// Telegram botga kelgan har bir xabarni shu yerga yuboradi (webhook). Tugma bosilsa ⏳ xabarini
// yuborib, GitHub'dagi "Yangiliklar" workflow'ini ishga tushiradi. Yangiliklarni GitHub'dagi bot.py yuboradi.
//
// Bot guruhga qo'shilsa yoki chiqarilsa, guruh.yml orqali guruhlar.json ro'yxatini yangilaydi
// (yangiliklar shu guruhlarga ham boradi). Faqat egasi qo'shgan guruhlar hisobga olinadi.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GITHUB_TOKEN, WEBHOOK_SECRET.
// GITHUB_TOKEN: fine-grained, faqat shu repo, Actions: Read and write.

const BUTTON = '🗞 Hozirgi yangiliklar';
const KEYBOARD = {keyboard: [[{text: BUTTON}]], resize_keyboard: true, is_persistent: true};
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
  if (String((msg.chat || {}).id) !== String(env.TELEGRAM_CHAT_ID)) return;  // boshqalarga javob yo'q
  const text = (msg.text || '').trim();
  if (text === BUTTON || text.startsWith('/yangiliklar')) {
    await startRun(env);
  } else if (text.startsWith('/start')) {
    await tg(env, 'sendMessage', {chat_id: env.TELEGRAM_CHAT_ID, reply_markup: KEYBOARD,
      text: `Yangiliklar har 3 soatda keladi. Oldinroq ko'rmoqchi bo'lsangiz, pastdagi «${BUTTON}» tugmasini bosing.`});
  }
}

const INSIDE = ['member', 'administrator', 'creator', 'restricted'];

async function handleMembership(m, env) {
  const chat = m.chat || {};
  if (chat.type === 'private') return;
  const name = chat.title || String(chat.id);
  const was = INSIDE.includes(m.old_chat_member.status);
  const now = INSIDE.includes(m.new_chat_member.status);
  if (!was && now) {
    if (String(m.from.id) !== String(env.TELEGRAM_CHAT_ID)) {  // begona qo'shgan bo'lsa, chiqib ketadi
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

async function startRun(env) {
  // Tugma ketma-ket bosilsa, navbatdagi yoki ishlayotgan ish bo'lsa, yangisi boshlanmaydi
  const runs = await github(env, `/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=5`)
    .then((r) => r.json()).catch(() => ({}));
  const busy = (runs.workflow_runs || []).some((r) => ['queued', 'in_progress', 'waiting', 'pending'].includes(r.status));
  if (busy) {
    await tg(env, 'sendMessage', {chat_id: env.TELEGRAM_CHAT_ID, text: '⏳ Yangiliklar allaqachon tayyorlanyapti, birozdan keyin keladi.'});
    return;
  }
  const wait = await tg(env, 'sendMessage', {chat_id: env.TELEGRAM_CHAT_ID, text: "⏳ Yangiliklar yig'ilyapti, 1-2 daqiqa..."});
  const waitId = wait.ok ? String(wait.result.message_id) : '';
  const res = await github(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: 'POST', body: JSON.stringify({ref: 'main', inputs: {wait_id: waitId}}),
  });
  if (res.status !== 204 && waitId) {
    await tg(env, 'editMessageText', {chat_id: env.TELEGRAM_CHAT_ID, message_id: Number(waitId),
      text: `⚠️ Bot ishga tushmadi (GitHub javobi ${res.status}).`});
  }
}
