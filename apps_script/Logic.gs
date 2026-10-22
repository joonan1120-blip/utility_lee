/**
 * Logic.gs — 판정·검증·공기압축기 가동 계산 (Google API 미사용, 순수 함수)
 * 기존 엑셀 '공기압축기_운용현황.xlsx' 계산 규칙을 이식했습니다.
 *  - 가동률 = 기간 부하시간 증가 ÷ 총 가동시간 증가 (총 = 부하 + 무부하)
 *  - 구간 계산시간 = MAX(측정 경과시간, 총 가동시간 증가)   ← 시간 자동 보정
 *  - 환산 사용시간: 일반형 = 부하 증가, 인버터 = 부하 증가 × 구간 로드율
 *      구간 로드율 = 양 끝 '운전 중' 로드율 평균(한쪽만 있으면 그 값),
 *      부하 증가 0 → 0, 양 끝 정지인데 부하 증가 → 최소/최대 비율(최소값 추정)
 *  - 기간 용량 사용률 = Σ환산 ÷ Σ계산시간
 */
var COUNTER_POINTS = ['부하 시간', '무부하 시간'];

function num_(v) {
  if (v === null || v === undefined) return null;
  var s = String(v).trim().replace(/,/g, '');
  if (s === '') return null;
  var f = Number(s);
  return isFinite(f) ? f : null;
}

function specText_(it) {
  var lo = it['하한'], hi = it['상한'], u = it['단위'] || '';
  if (it['입력형태'] === '선택') return it['관리SPEC'] || '';
  if (lo !== '' && hi !== '') return (lo + '~' + hi + ' ' + u).trim();
  if (hi !== '') return (hi + ' 이하 ' + u).trim();
  if (lo !== '') return (lo + ' 이상 ' + u).trim();
  return it['관리SPEC'] || u;
}

/** 정상 / 이탈 / 기록(기준 없음) / '' */
function judge_(it, value) {
  if (value === null || value === undefined || String(value).trim() === '') return '';
  if (it['입력형태'] === '선택') {
    var ok = String(it['정상값'] || '').split(',').filter(String);
    if (!ok.length) return '기록';
    return ok.indexOf(String(value)) >= 0 ? '정상' : '이탈';
  }
  var v = num_(value);
  if (v === null) return '';
  var lo = num_(it['하한']), hi = num_(it['상한']);
  if (lo === null && hi === null) return '기록';
  if ((lo !== null && v < lo) || (hi !== null && v > hi)) return '이탈';
  return '정상';
}

function hoursOf_(d, t) {
  var p = d.split('-').map(Number);
  var hm = /^\d{1,2}:\d{2}$/.test(t || '') ? t.split(':').map(Number) : [0, 0];
  return Date.UTC(p[0], p[1] - 1, p[2], hm[0], hm[1]) / 3600000;
}

/** 점검기록 행(객체) → 압축기 측정값 [{캠퍼스,호기,d,t,hasTime,tot,load,run}] */
function compressorReadings_(logRows, comps) {
  var isComp = {};
  comps.forEach(function (c) { isComp[c['캠퍼스'] + '|' + c['설비명']] = true; });
  var m = {};
  logRows.forEach(function (r) {
    var p = r['점검항목'];
    if (COUNTER_POINTS.indexOf(p) < 0 && p !== '운전값') return;
    var key = r['캠퍼스'] + '|' + r['설비명'];
    if (!isComp[key]) return;
    var k = key + '|' + r['점검일'];
    if (!m[k]) m[k] = { 캠퍼스: r['캠퍼스'], 호기: r['설비명'], d: r['점검일'], t: '' };
    m[k][p] = r['값'];
    if (p === '부하 시간') m[k].t = r['점검시각'] || '';
  });
  var out = [];
  Object.keys(m).forEach(function (k) {
    var x = m[k], load = num_(x['부하 시간']), unload = num_(x['무부하 시간']);
    if (load === null || unload === null || !/^\d{4}-\d{2}-\d{2}$/.test(x.d)) return;
    out.push({ 캠퍼스: x['캠퍼스'], 호기: x['호기'], d: x.d, t: x.t,
      hasTime: /^\d{1,2}:\d{2}$/.test(x.t), h: hoursOf_(x.d, x.t),
      tot: load + unload, load: load, run: num_(x['운전값']) });
  });
  out.sort(function (a, b) {
    return a['캠퍼스'] < b['캠퍼스'] ? -1 : a['캠퍼스'] > b['캠퍼스'] ? 1 :
      a['호기'] < b['호기'] ? -1 : a['호기'] > b['호기'] ? 1 : a.h - b.h;
  });
  return out;
}

function eqInfo_(c) {
  return { 형식: c['형식'], min: num_(c['최소운전값']), max: num_(c['최대운전값']), 단위: c['단위'] || '' };
}

function invRatio_(run, eq) {
  if (run === null || !eq.max) return null;
  if (run === 0) return 0;
  if (eq.min !== null && !(run >= eq.min && run <= eq.max)) return null;
  return run / eq.max;
}

function segments_(rows, eq) {
  var segs = [];
  for (var i = 1; i < rows.length; i++) {
    var p = rows[i - 1], c = rows[i];
    var elapsed = (p.hasTime && c.hasTime) ? c.h - p.h : hoursOf_(c.d, '') - hoursOf_(p.d, '');
    var dTot = c.tot - p.tot, dLoad = c.load - p.load, calcH = Math.max(elapsed, dTot);
    var st = '정상';
    if (dTot < 0 || dLoad < 0) st = '누적값 감소';
    else if (dLoad > dTot) st = '부하증가량 확인';
    var ratio = null, used = null, cap = '정상';
    if (st === '정상') {
      if (eq['형식'] === '일반형') ratio = 1;
      else {
        var qc = invRatio_(c.run, eq), qp = invRatio_(p.run, eq);
        var z = (qp !== null && qp > 0) ? qp : null;
        if (dLoad === 0) ratio = 0;
        else if (qc !== null && qc > 0) ratio = z !== null ? (qc + z) / 2 : qc;
        else if (z !== null) ratio = z;
        else if (qc === 0 && qp === 0 && eq.min && eq.max) { ratio = eq.min / eq.max; cap = '최소값 추정'; }
        else cap = '운전값 부족';
      }
      if (ratio !== null) used = dLoad === 0 ? 0 : dLoad * ratio;
    } else cap = st;
    segs.push({ 캠퍼스: c['캠퍼스'], 호기: c['호기'], 종료일: c.d, 경과: elapsed, 총증가: dTot,
      부하증가: dLoad, 계산시간: calcH, 보정: calcH - elapsed > 1e-7 ? calcH - elapsed : 0,
      환산: used, 상태: st, 용량상태: cap });
  }
  return segs;
}

/** 공기압축기 집계. start/end: 'YYYY-MM-DD' 또는 '' */
function computeCompressors_(readings, comps, start, end) {
  var active = comps.filter(function (c) { return String(c['사용']) !== '0'; });
  var inRange = readings.filter(function (r) {
    return (!start || r.d >= start) && (!end || r.d <= end);
  });
  var units = [], allSegs = [];
  active.forEach(function (c) {
    var eq = eqInfo_(c);
    var g = inRange.filter(function (r) { return r['캠퍼스'] === c['캠퍼스'] && r['호기'] === c['설비명']; });
    var ga = readings.filter(function (r) { return r['캠퍼스'] === c['캠퍼스'] && r['호기'] === c['설비명']; });
    var s = segments_(g, eq);
    allSegs = allSegs.concat(s);
    var u = { 캠퍼스: c['캠퍼스'], 호기: c['설비명'], 형식: c['형식'], 건수: g.length, 시작일: null, 종료일: null,
      기간총: null, 기간부하: null, 가동률: null, 용량사용률: null, 최근로드율: null, 최근측정일: null, 상태: '',
      계산시간: 0, 환산: 0 };
    if (ga.length) {
      var last = ga[ga.length - 1];
      u.최근측정일 = last.d;
      if (c['형식'] === '인버터형') u.최근로드율 = invRatio_(last.run, eq);
      else if (ga.length >= 2) {
        var pv = ga[ga.length - 2], dt = last.tot - pv.tot;
        u.최근로드율 = dt === 0 ? 0 : (last.load - pv.load) / dt;
      }
    }
    if (g.length < 2) u.상태 = g.length ? '기간 내 2회 입력 필요' : '미입력';
    else if (s.some(function (x) { return x.상태 !== '정상'; })) u.상태 = '입력 오류 확인';
    else {
      u.시작일 = g[0].d; u.종료일 = g[g.length - 1].d;
      u.기간총 = g[g.length - 1].tot - g[0].tot;
      u.기간부하 = g[g.length - 1].load - g[0].load;
      u.가동률 = u.기간총 ? u.기간부하 / u.기간총 : null;
      if (s.some(function (x) { return x.용량상태 === '운전값 부족'; })) u.상태 = '운전값 부족';
      else {
        var ch = s.reduce(function (a, x) { return a + x.계산시간; }, 0);
        u.용량사용률 = ch ? s.reduce(function (a, x) { return a + x.환산; }, 0) / ch : null;
        u.상태 = s.some(function (x) { return x.용량상태 === '최소값 추정'; }) ? '최소값 추정' : '정상';
      }
    }
    u.계산시간 = s.reduce(function (a, x) { return a + x.계산시간; }, 0);
    u.환산 = s.reduce(function (a, x) { return a + (x.환산 || 0); }, 0);
    units.push(u);
  });

  var campusNames = [];
  units.forEach(function (u) { if (campusNames.indexOf(u.캠퍼스) < 0) campusNames.push(u.캠퍼스); });
  var campus = campusNames.map(function (cp) {
    var us = units.filter(function (u) { return u.캠퍼스 === cp; });
    var all = function (f) { return us.every(f); };
    var sum = function (k) { return us.reduce(function (a, u) { return a + (u[k] || 0); }, 0); };
    var c = { 캠퍼스: cp, 대수: us.length, 입력호기: us.filter(function (u) { return u.건수 > 0; }).length };
    var ok = all(function (u) { return u.가동률 !== null; });
    c.기간총 = ok ? sum('기간총') : null; c.기간부하 = ok ? sum('기간부하') : null;
    c.가동률 = c.기간총 ? c.기간부하 / c.기간총 : null;
    c.용량사용률 = all(function (u) { return u.용량사용률 !== null; }) && sum('계산시간') ? sum('환산') / sum('계산시간') : null;
    c.최근사용률 = all(function (u) { return u.최근로드율 !== null; }) ? sum('최근로드율') / us.length : null;
    var ds = us.map(function (u) { return u.시작일; }).filter(Boolean).sort();
    var de = us.map(function (u) { return u.종료일; }).filter(Boolean).sort();
    c.시작일 = ds[0] || null; c.종료일 = de[de.length - 1] || null;
    c.보정시간 = allSegs.filter(function (x) { return x.캠퍼스 === cp; }).reduce(function (a, x) { return a + x.보정; }, 0);
    var bad = us.filter(function (u) { return u.상태 !== '정상' && u.상태 !== '최소값 추정'; });
    c.상태 = !bad.length ? '정상' : all(function (u) { return u.건수 === 0; }) ? '미입력' :
      '확인 필요: ' + bad.map(function (u) { return u.호기 + ' ' + u.상태; }).join(', ');
    return c;
  });

  var tm = {};
  allSegs.filter(function (x) { return x.상태 === '정상'; }).forEach(function (x) {
    var k = x.캠퍼스 + '|' + x.종료일;
    if (!tm[k]) tm[k] = { 캠퍼스: x.캠퍼스, 날짜: x.종료일, 부하: 0, 총: 0, 환산: 0, 시간: 0 };
    tm[k].부하 += x.부하증가; tm[k].총 += x.총증가; tm[k].환산 += x.환산 || 0; tm[k].시간 += x.계산시간;
  });
  var trend = Object.keys(tm).sort().map(function (k) {
    var t = tm[k];
    return { 캠퍼스: t.캠퍼스, 날짜: t.날짜, 가동률: t.총 > 0 ? t.부하 / t.총 : null,
      용량사용률: t.시간 > 0 ? t.환산 / t.시간 : null };
  });
  return { campus: campus, units: units, trend: trend };
}

/** 압축기 누적값 검증 (엑셀 '입력 확인' 규칙). 같은 날 재입력은 수정으로 처리 */
function validateCounters_(readings, c, d, t, tot, load, run) {
  var errs = [], warns = [];
  if (load > tot) errs.push('부하 시간이 총 가동시간보다 큽니다');
  var eq = eqInfo_(c);
  if (eq['형식'] === '인버터형') {
    if (run === null) errs.push('운전값(' + eq.단위 + ')을 입력하세요 (정지 시 0)');
    else if (run !== 0 && eq.min !== null && eq.max !== null && !(run >= eq.min && run <= eq.max))
      errs.push('운전값은 0 또는 ' + eq.min + '~' + eq.max + ' ' + eq.단위 + ' 범위여야 합니다');
  }
  var g = readings.filter(function (r) { return r['캠퍼스'] === c['캠퍼스'] && r['호기'] === c['설비명'] && r.d !== d; });
  var h = hoursOf_(d, t);
  var prev = g.filter(function (r) { return r.h < h; }).pop();
  var next = g.filter(function (r) { return r.h > h; })[0];
  if (prev) {
    var dTot = tot - prev.tot, dLoad = load - prev.load;
    if (dTot < 0) errs.push('총 가동시간(부하+무부하)이 직전값(' + prev.tot.toLocaleString() + 'h)보다 작습니다');
    if (dLoad < 0) errs.push('부하 시간이 직전값(' + prev.load.toLocaleString() + 'h)보다 작습니다');
    if (dTot >= 0 && dLoad > dTot) errs.push('부하 증가(' + dLoad + 'h)가 총시간 증가(' + dTot + 'h)보다 큽니다');
    var el = h - prev.h;
    if (dTot > el + 1) warns.push('총시간 증가 ' + dTot + 'h > 경과 ' + el.toFixed(1) + 'h — 계기값·시각 확인 (계산 시 자동 보정)');
  }
  if (next) {
    if (tot > next.tot || load > next.load) errs.push('이후 측정값(' + next.d + ')보다 큽니다');
    warns.push('최신 측정일보다 이전 날짜입니다');
  }
  return { errors: errs, warns: warns };
}

/** 설비 1대 입력 검증. values: {점검항목: 값} */
function validateGroup_(eqItems, values, logRows, comps, campus, equip, d, t) {
  var errs = [], warns = [], judged = {};
  eqItems.forEach(function (it) {
    var k = it['점검항목'], v = values[k];
    if (v === undefined || v === null || String(v).trim() === '') return;
    if (it['입력형태'] === '숫자' && num_(v) === null) { errs.push(k + ': 숫자를 입력하세요'); return; }
    if (it['입력형태'] === '선택' && String(it['선택지']).split(',').indexOf(String(v)) < 0) {
      errs.push(k + ': 선택지에 없는 값'); return;
    }
    judged[k] = judge_(it, v);
    if (judged[k] === '이탈') warns.push(k + ' ' + v + ' — 관리 SPEC(' + specText_(it) + ') 이탈');
  });
  var c = comps.filter(function (x) { return x['캠퍼스'] === campus && x['설비명'] === equip; })[0];
  if (c && (values['부하 시간'] !== undefined || values['무부하 시간'] !== undefined)) {
    var load = num_(values['부하 시간']), unload = num_(values['무부하 시간']);
    if ((load === null) !== (unload === null)) errs.push('부하 시간·무부하 시간은 함께 입력하세요 (가동률 계산용)');
    else if (load !== null) {
      var r = validateCounters_(compressorReadings_(logRows, comps), c, d, t, load + unload, load, num_(values['운전값']));
      errs = errs.concat(r.errors); warns = warns.concat(r.warns);
    }
  } else if (c && c['형식'] === '인버터형' && values['운전값'] !== undefined) {
    var eq = eqInfo_(c), rv = num_(values['운전값']);
    if (rv !== null && rv !== 0 && eq.min !== null && !(rv >= eq.min && rv <= eq.max))
      errs.push('운전값은 0 또는 ' + eq.min + '~' + eq.max + ' ' + eq.단위 + ' 범위여야 합니다');
  }
  return { errors: errs, warns: warns, judged: judged };
}
