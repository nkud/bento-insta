/**
 * 在宅だんな弁当：Google Apps Script から GitHub Actions を確実に起動する
 *
 * GitHub の定期実行（cron）は遅れたり飛んだりするので、Google 側のタイマーで見張る。
 *   - 5分ごと : Drive のお弁当フォルダに新しい写真があれば「下書き作成」を起動
 *               予約時刻（/post 19:30 など）を過ぎた下書きがあっても起動
 *   - 毎日 4時台 : 「反応を集計・分析」を起動（月曜だけ Claude の考察も更新）
 *
 * 使い方は README の「Apps Script で起動を安定させる」を参照。
 */

const REPO = 'nkud/bento-insta';
const POST_WORKFLOW = 'post.yml';
const METRICS_WORKFLOW = 'metrics.yml';
const IMAGE_EXT = /\.(jpe?g|png|heic|heif|webp)$/i;

function props_() {
  return PropertiesService.getScriptProperties();
}

/** 最初に1回だけ実行：タイマーを登録し、今ある写真を「既知」として記録する */
function setup() {
  const p = props_();
  if (!p.getProperty('GITHUB_TOKEN') || !p.getProperty('FOLDER_ID')) {
    throw new Error('スクリプトのプロパティに GITHUB_TOKEN と FOLDER_ID を登録してください');
  }
  ScriptApp.getProjectTriggers().forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('checkAndDispatch').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('dailyMetrics').timeBased().atHour(4).everyDays(1).inTimezone('Asia/Tokyo').create();
  // 今フォルダにある写真のうち、まだ下書きになっていないものは次の確認で拾う
  p.setProperty('SEEN_IDS', JSON.stringify([]));
  checkAndDispatch();
  Logger.log('設定しました。5分ごとの確認と、毎日4時の集計を登録しました。');
}

/** 5分ごと：新しい写真 or 予約時刻を過ぎた下書きがあれば、下書き作成ワークフローを起動 */
function checkAndDispatch() {
  const p = props_();
  const seen = new Set(JSON.parse(p.getProperty('SEEN_IDS') || '[]'));
  const posts = fetchPosts_();
  const known = new Set(posts.map((r) => r.drive_file_id));

  const folder = DriveApp.getFolderById(p.getProperty('FOLDER_ID'));
  const files = folder.getFiles();
  const fresh = [];
  while (files.hasNext()) {
    const f = files.next();
    const isImage = (f.getMimeType() || '').indexOf('image/') === 0 || IMAGE_EXT.test(f.getName());
    if (isImage && !known.has(f.getId()) && !seen.has(f.getId())) fresh.push(f.getId());
  }

  const now = new Date();
  const due = posts.filter((r) => r.status === 'scheduled' && r.scheduled_at && new Date(r.scheduled_at) <= now);
  const retry = posts.filter((r) => r.status === 'failed' && (r.attempts || 0) < 3);

  if (!fresh.length && !due.length && !retry.length) return;

  // 実行中・直後の重複起動を防ぐ（4分以内は待つ）
  const last = Number(p.getProperty('LAST_DISPATCH') || 0);
  if (Date.now() - last < 4 * 60 * 1000) return;

  if (dispatch_(POST_WORKFLOW, {})) {
    p.setProperty('LAST_DISPATCH', String(Date.now()));
    fresh.forEach((id) => seen.add(id));
    p.setProperty('SEEN_IDS', JSON.stringify([...seen].slice(-500)));
    Logger.log(`起動しました（新しい写真 ${fresh.length} 枚・予約 ${due.length} 件・再挑戦 ${retry.length} 件）`);
  }
}

/** 毎日4時台：反応の集計。月曜だけ Claude の考察も更新する */
function dailyMetrics() {
  const monday = new Date().getDay() === 1;
  dispatch_(METRICS_WORKFLOW, {with_llm: monday ? 'true' : 'false'});
}

/** GitHub の投稿記録（公開リポジトリの data/posts.json）を読む */
function fetchPosts_() {
  const url = `https://raw.githubusercontent.com/${REPO}/main/data/posts.json?t=${Date.now()}`;
  const res = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
  if (res.getResponseCode() !== 200) return [];
  try {
    return JSON.parse(res.getContentText());
  } catch (e) {
    return [];
  }
}

function dispatch_(workflow, inputs) {
  const token = props_().getProperty('GITHUB_TOKEN');
  const res = UrlFetchApp.fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: 'post',
    contentType: 'application/json',
    headers: {Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json'},
    payload: JSON.stringify({ref: 'main', inputs: inputs}),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  if (code !== 204) {
    Logger.log(`GitHub の起動に失敗しました（${code}）: ${res.getContentText()}`);
    return false;
  }
  return true;
}

/** 動作確認用：今すぐ下書き作成を起動する */
function runNow() {
  Logger.log(dispatch_(POST_WORKFLOW, {}) ? '起動しました' : '失敗しました（ログを確認）');
}
