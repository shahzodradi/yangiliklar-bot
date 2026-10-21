// Telegram'dagi "🗞 Hozirgi yangiliklar" tugmasi uchun ko'prik (Google Apps Script, bepul).
// Har daqiqada botga kelgan xabarlarni tekshiradi; tugma bosilgan bo'lsa ⏳ xabarini yuborib,
// GitHub'dagi "Yangiliklar" workflow'ini ishga tushiradi. Yangiliklarni GitHub'dagi bot.py yuboradi.
//
// O'rnatish: shu kodni script.google.com dagi yangi loyihaga qo'ying, CONFIG ni to'ldiring,
// yuqoridagi ro'yxatdan "setup" ni tanlab Run bosing va ruxsat bering. Shundan keyin o'zi ishlaydi.

const CONFIG = {
  TELEGRAM_BOT_TOKEN: '',
  TELEGRAM_CHAT_ID: '',
  GITHUB_REPO: '',   // masalan "username/yangiliklar-bot"
  GITHUB_TOKEN: '',  // fine-grained token: faqat shu repo, Actions: Read and write
};

const BUTTON = '🗞 Hozirgi yangiliklar';
const KEYBOARD = {keyboard: [[{text: BUTTON}]], resize_keyboard: true, is_persistent: true};
const REPEAT_GUARD_MS = 2 * 60 * 1000;  // 2 daqiqa ichidagi qayta bosish yangi ish boshlamaydi

function tg_(method, payload) {
  const res = UrlFetchApp.fetch('https://api.telegram.org/bot' + CONFIG.TELEGRAM_BOT_TOKEN + '/' + method, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(payload || {}),
    muteHttpExceptions: true,
  });
  return JSON.parse(res.getContentText());
}

function poll() {
  const props = PropertiesService.getScriptProperties();
  let offset = Number(props.getProperty('offset') || 0);
  const res = tg_('getUpdates', {offset: offset, timeout: 0, allowed_updates: ['message']});
  if (!res.ok) return;
  let pressed = false;
  res.result.forEach(function (u) {
    offset = u.update_id + 1;
    const msg = u.message || {};
    if (String((msg.chat || {}).id) !== String(CONFIG.TELEGRAM_CHAT_ID)) return;  // boshqalarga javob yo'q
    const text = (msg.text || '').trim();
    if (text === BUTTON || text.indexOf('/yangiliklar') === 0) {
      pressed = true;
    } else if (text.indexOf('/start') === 0) {
      tg_('sendMessage', {chat_id: CONFIG.TELEGRAM_CHAT_ID, reply_markup: KEYBOARD,
        text: "Yangiliklar har 3 soatda keladi. Oldinroq ko'rmoqchi bo'lsangiz, pastdagi «" + BUTTON + "» tugmasini bosing."});
    }
  });
  props.setProperty('offset', String(offset));
  if (pressed) startRun_(props);
}

function startRun_(props) {
  const last = Number(props.getProperty('lastRun') || 0);
  if (Date.now() - last < REPEAT_GUARD_MS) return;
  const wait = tg_('sendMessage', {chat_id: CONFIG.TELEGRAM_CHAT_ID, text: "⏳ Yangiliklar yig'ilyapti, 1-2 daqiqa..."});
  const waitId = wait.ok ? String(wait.result.message_id) : '';
  const res = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + CONFIG.GITHUB_REPO + '/actions/workflows/yangiliklar.yml/dispatches', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: {Authorization: 'Bearer ' + CONFIG.GITHUB_TOKEN, Accept: 'application/vnd.github+json'},
      payload: JSON.stringify({ref: 'main', inputs: {wait_id: waitId}}),
    });
  if (res.getResponseCode() !== 204) {
    if (waitId) {
      tg_('editMessageText', {chat_id: CONFIG.TELEGRAM_CHAT_ID, message_id: Number(waitId),
        text: '⚠️ Bot ishga tushmadi (GitHub javobi ' + res.getResponseCode() + ').'});
    }
    return;
  }
  props.setProperty('lastRun', String(Date.now()));
}

// Bir marta qo'lda ishga tushiriladi: har daqiqalik tekshiruvni yoqadi.
function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('poll').timeBased().everyMinutes(1).create();
  tg_('deleteWebhook', {});
  tg_('setMyCommands', {commands: [{command: 'yangiliklar', description: "Hozirgi yangiliklarni ko'rish"}]});
  tg_('sendMessage', {chat_id: CONFIG.TELEGRAM_CHAT_ID, reply_markup: KEYBOARD,
    text: '✅ Tugma endi bulut orqali ishlaydi: kompyuter o\'chiq bo\'lsa ham javob beradi.'});
  poll();
}
