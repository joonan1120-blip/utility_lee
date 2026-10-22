/**
 * Code.gs — Utility 운전일지 웹앱 (Google Apps Script 시연판)
 *
 * 설치: 새 Google 시트 → 확장 프로그램 > Apps Script → 파일 4개(Code.gs, Logic.gs, Seed.gs, Index.html) 붙여넣기
 *       → 함수 선택 'setup' 실행(권한 승인) → 배포 > 새 배포 > 웹 앱
 *          (실행: 나 / 액세스 권한: 모든 사용자) → 생성된 URL을 현장에 공유
 * PIN 변경: 프로젝트 설정 > 스크립트 속성 > PIN
 */
var SHEETS = { items: '점검항목', comps: '압축기설정', log: '점검기록' };
var LOG_HEADER = ['기록ID', '저장시각', '점검일', '점검시각', '캠퍼스', '설비명', '점검항목', '값', '판정', '점검자', '메모'];
var TZ = 'Asia/Seoul';

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Utility 운전일지')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}

// ---------------- 시트 IO ----------------
function sheet_(name) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh) throw new Error("'" + name + "' 시트가 없습니다. setup()을 먼저 실행하세요.");
  return sh;
}

function readTable_(name) {
  var v = sheet_(name).getDataRange().getDisplayValues();
  if (v.length < 2) return [];
  var h = v[0];
  return v.slice(1).filter(function (r) { return r.join('') !== ''; }).map(function (r) {
    var o = {}; h.forEach(function (k, i) { o[k] = r[i]; }); return o;
  });
}

function writeRows_(sh, startRow, rows) {
  if (!rows.length) return;
  var rg = sh.getRange(startRow, 1, rows.length, rows[0].length);
  rg.setNumberFormat('@');          // 날짜·시각이 자동 변환되지 않도록 텍스트로 저장
  rg.setValues(rows);
}

function checkPin_(pin) {
  var p = PropertiesService.getScriptProperties().getProperty('PIN') || '1234';
  if (String(pin) !== String(p)) throw new Error('PIN이 맞지 않습니다');
}

function nowKst_() {
  var d = new Date();
  return { date: Utilities.formatDate(d, TZ, 'yyyy-MM-dd'), time: Utilities.formatDate(d, TZ, 'HH:mm'),
    stamp: Utilities.formatDate(d, TZ, 'yyyy-MM-dd HH:mm:ss') };
}

// ---------------- API (google.script.run) ----------------
function apiInit(pin) {
  checkPin_(pin);
  return { items: readTable_(SHEETS.items), comps: readTable_(SHEETS.comps), now: nowKst_() };
}

/** 캠퍼스·날짜별 오늘 입력값과 직전값 */
function apiDay(pin, campus, d) {
  checkPin_(pin);
  var today = {}, prev = {};
  readTable_(SHEETS.log).forEach(function (r) {
    if (r['캠퍼스'] !== campus) return;
    var k = r['설비명'] + '|' + r['점검항목'];
    if (r['점검일'] === d) today[k] = { 값: r['값'], 판정: r['판정'], 점검자: r['점검자'] };
    else if (r['점검일'] < d && (!prev[k] || prev[k].d <= r['점검일'])) prev[k] = { v: r['값'], d: r['점검일'] };
  });
  return { today: today, prev: prev };
}

/** 설비 1대 저장. p = {campus, equip, date, time, inspector, memo, values:{항목:값}} */
function apiSave(pin, p) {
  checkPin_(pin);
  if (!p.inspector) return { ok: false, errors: ['점검자 이름을 입력하세요'] };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(p.date)) return { ok: false, errors: ['점검일 형식 오류'] };
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var items = readTable_(SHEETS.items).filter(function (i) { return i['캠퍼스'] === p.campus && i['설비명'] === p.equip; });
    if (!items.length) return { ok: false, errors: ['설비를 찾을 수 없습니다'] };
    var values = {};
    items.forEach(function (it) {
      var v = p.values[it['점검항목']];
      if (v !== undefined && v !== null && String(v).trim() !== '') values[it['점검항목']] = String(v).trim();
    });
    if (!Object.keys(values).length) return { ok: false, errors: ['입력된 값이 없습니다'] };
    var logRows = readTable_(SHEETS.log);
    var res = validateGroup_(items, values, logRows, readTable_(SHEETS.comps), p.campus, p.equip, p.date, p.time);
    if (res.errors.length) return { ok: false, errors: res.errors, warns: res.warns };

    var sh = sheet_(SHEETS.log);
    var all = sh.getDataRange().getDisplayValues();
    var idx = {};
    for (var i = 1; i < all.length; i++) idx[[all[i][2], all[i][4], all[i][5], all[i][6]].join('|')] = i + 1;
    var stamp = nowKst_().stamp, appends = [];
    Object.keys(values).forEach(function (k) {
      var line = ['R' + Utilities.getUuid().slice(0, 10), stamp, p.date, p.time, p.campus, p.equip, k,
        values[k], res.judged[k] || '', p.inspector, p.memo || ''];
      var row = idx[[p.date, p.campus, p.equip, k].join('|')];
      if (row) { line[0] = all[row - 1][0]; writeRows_(sh, row, [line]); }   // 같은 날 재입력 = 수정
      else appends.push(line);
    });
    writeRows_(sh, sh.getLastRow() + 1, appends);
    return { ok: true, warns: res.warns, judged: res.judged, count: Object.keys(values).length };
  } finally {
    lock.releaseLock();
  }
}

/** 날짜별 현황: 입력률, 이탈 목록, 미완료 설비 */
function apiDash(pin, d) {
  checkPin_(pin);
  var items = readTable_(SHEETS.items), log = readTable_(SHEETS.log);
  var day = {};
  log.forEach(function (r) { if (r['점검일'] === d) day[r['캠퍼스'] + '|' + r['설비명'] + '|' + r['점검항목']] = r; });
  var camp = {}, dev = [];
  items.forEach(function (it) {
    var c = it['캠퍼스'];
    if (!camp[c]) camp[c] = { 캠퍼스: c, 전체: 0, 입력: 0, 이탈: 0, eq: {} };
    var e = camp[c].eq[it['설비명']] || (camp[c].eq[it['설비명']] = { n: 0, done: 0 });
    var r = day[c + '|' + it['설비명'] + '|' + it['점검항목']];
    camp[c].전체++; e.n++;
    if (r && r['값'] !== '') {
      camp[c].입력++; e.done++;
      if (r['판정'] === '이탈') {
        camp[c].이탈++;
        dev.push({ 캠퍼스: c, 설비명: it['설비명'], 점검항목: it['점검항목'], 값: r['값'], SPEC: specText_(it), 점검자: r['점검자'] });
      }
    }
  });
  var out = Object.keys(camp).map(function (c) {
    var x = camp[c];
    x.미완료 = Object.keys(x.eq).filter(function (k) { return x.eq[k].done < x.eq[k].n; })
      .map(function (k) { return k + ' (' + x.eq[k].done + '/' + x.eq[k].n + ')'; });
    x.설비수 = Object.keys(x.eq).length; delete x.eq;
    return x;
  });
  return { campus: out, deviations: dev };
}

/** 항목 추이 (최근 60일) */
function apiSeries(pin, campus, equip, point, d) {
  checkPin_(pin);
  var from = Utilities.formatDate(new Date(new Date(d + 'T00:00:00+09:00').getTime() - 60 * 864e5), TZ, 'yyyy-MM-dd');
  return readTable_(SHEETS.log).filter(function (r) {
    return r['캠퍼스'] === campus && r['설비명'] === equip && r['점검항목'] === point &&
      r['점검일'] >= from && r['점검일'] <= d && num_(r['값']) !== null;
  }).map(function (r) { return { d: r['점검일'], v: num_(r['값']), who: r['점검자'] }; })
    .sort(function (a, b) { return a.d < b.d ? -1 : 1; });
}

/** 공기압축기 가동 분석 */
function apiComp(pin, start, end) {
  checkPin_(pin);
  var comps = readTable_(SHEETS.comps);
  var rd = compressorReadings_(readTable_(SHEETS.log), comps);
  var res = computeCompressors_(rd, comps, start || '', end || '');
  res.range = rd.length ? [rd.map(function (r) { return r.d; }).sort()[0],
    rd.map(function (r) { return r.d; }).sort().pop()] : null;
  return res;
}

// ---------------- 최초 설정 ----------------
/** 시트 3장 생성 + 기본 데이터(운전일지 항목, 압축기 설정, 기존 엑셀 측정값) 입력. 여러 번 실행해도 기존 기록은 보존 */
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var specs = [
    [SHEETS.items, Object.keys(SEED.items[0]), SEED.items],
    [SHEETS.comps, Object.keys(SEED.compressors[0]), SEED.compressors],
    [SHEETS.log, LOG_HEADER, SEED.log]
  ];
  specs.forEach(function (s) {
    var sh = ss.getSheetByName(s[0]) || ss.insertSheet(s[0]);
    if (sh.getLastRow() > 1) return;   // 이미 데이터가 있으면 건드리지 않음
    var rows = [s[1]].concat(s[2].map(function (o) { return s[1].map(function (k) { return o[k] === undefined ? '' : String(o[k]); }); }));
    writeRows_(sh, 1, rows);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, s[1].length).setFontWeight('bold').setBackground('#eceae4');
  });
  var props = PropertiesService.getScriptProperties();
  if (!props.getProperty('PIN')) props.setProperty('PIN', '1234');
  var first = ss.getSheetByName('Sheet1') || ss.getSheetByName('시트1');
  if (first && first.getLastRow() === 0 && ss.getSheets().length > 3) ss.deleteSheet(first);
  return '설정 완료: 점검항목 ' + SEED.items.length + '개, 압축기 ' + SEED.compressors.length + '대';
}
