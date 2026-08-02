/* 픽셀 오피스 헤드리스 하네스 — 브라우저 없이 index.html 의 스크립트를 실제로 돌린다.
 *
 * 왜 있나: 이 서버에는 브라우저가 없다. 그래서 "문법은 맞는데 첫 프레임에서
 * 죽는" 콘솔을 그대로 배포한 적이 있다. 여기서는 DOM·캔버스를 최소로 흉내 내고
 * **진짜 상태(JSON)** 를 먹여 draw() 까지 돌린 뒤, 사람이 어느 섹터에 서 있는지
 * 좌표로 확인한다. 그림이 예쁜지는 여전히 못 본다 — 죽지 않는지, 위치가 스팬과
 * 맞는지를 본다.
 *
 * 쓰는 쪽: aoc/tests/test_pixel_office.py (quickjs 가 있으면 실행, 없으면 skip)
 * 파이썬이 자리표시자 세 개를 치환해 넣는다: 상태 JSON, 트레이스 JSON,
 * 그리고 index.html 의 스크립트 본문.
 */

var window = globalThis;
var devicePixelRatio = 2;
var CALLS = { fillRect: 0, poly: 0, text: 0, warn: [] };

/* 그린 것을 그대로 받아 적는다 — 브라우저가 없는 호스트에서 화면을 실제로
 * 되살려 보려고(scripts/office-preview.py). 검증 자체는 좌표만으로도 되지만,
 * "구도가 사무실처럼 보이는가"는 눈으로 봐야 한다. */
var DL = [];
var REC = false;

function ctxStub() {
  var c = {
    fillStyle: "", strokeStyle: "", lineWidth: 1, font: "", textAlign: "", textBaseline: "",
    globalAlpha: 1, _path: [],
    fillRect: function (x, y, w, h) {
      CALLS.fillRect++;
      if (!isFinite(x) || !isFinite(y) || !isFinite(w) || !isFinite(h))
        CALLS.warn.push("fillRect NaN");
      if (REC && this._main) DL.push(["r", x, y, w, h, this.fillStyle, this.globalAlpha]);
    },
    strokeRect: function (x, y, w, h) {
      if (REC && this._main) DL.push(["sr", x, y, w, h, this.strokeStyle, this.globalAlpha, this.lineWidth]);
    },
    clearRect: function () {},
    beginPath: function () { this._path = []; },
    closePath: function () {},
    moveTo: function (x, y) {
      if (!isFinite(x) || !isFinite(y)) CALLS.warn.push("moveTo NaN");
      this._path.push([x, y]);
    },
    lineTo: function (x, y) {
      if (!isFinite(x) || !isFinite(y)) CALLS.warn.push("lineTo NaN");
      this._path.push([x, y]);
    },
    fill: function () {
      CALLS.poly++;
      if (REC && this._main && this._path.length > 2)
        DL.push(["p", this._path.slice(), this.fillStyle, this.globalAlpha]);
    },
    stroke: function () {
      if (REC && this._main && this._path.length > 1)
        DL.push(["l", this._path.slice(), this.strokeStyle, this.globalAlpha, this.lineWidth]);
    },
    arc: function (x, y, r, a0, a1) {
      for (var i = 0; i <= 16; i++)
        this._path.push([x + r * Math.cos(i / 16 * 6.28318), y + r * Math.sin(i / 16 * 6.28318)]);
    },
    ellipse: function (x, y, rx, ry) {
      for (var i = 0; i <= 20; i++)
        this._path.push([x + rx * Math.cos(i / 20 * 6.28318), y + ry * Math.sin(i / 20 * 6.28318)]);
    },
    save: function () {}, restore: function () {}, setTransform: function () {},
    fillText: function (t, x, y) {
      CALLS.text++;
      if (!isFinite(x) || !isFinite(y)) CALLS.warn.push("fillText NaN: " + t);
      if (REC && this._main) DL.push(["t", String(t), x, y, this.fillStyle, this.globalAlpha,
                        this.font, this.textAlign]);
    },
    measureText: function (t) { return { width: String(t).length * 6 }; },
  };
  return c;
}

/* 한 프레임을 받아 적어 돌려준다. */
function record(view, divId, agentId, clockNs) {
  if (view) S.view = view;
  if (divId) S.div = divId;
  if (agentId) S.agent = agentId;
  if (clockNs) setClock(clockNs, true);
  if (view === "building") fitCam(); else focusLevel(levelByDiv(S.div));
  DL = []; REC = true;
  draw();
  REC = false;
  var r = DOM.cv.getBoundingClientRect();
  return JSON.stringify({ w: r.width, h: r.height, ops: DL });
}

function el(id) {
  var e = {
    id: id, innerHTML: "", textContent: "", value: "0", max: "1000", min: "0",
    disabled: false, offsetWidth: 120, offsetHeight: 40, dataset: {},
    style: {}, classList: { add: function () {}, remove: function () {} },
    addEventListener: function (k, fn) { (this._ev = this._ev || {})[k] = fn; },
    getBoundingClientRect: function () { return { width: 1280, height: 720, left: 0, top: 0 }; },
    getContext: function () {
      if (!this._ctx) { this._ctx = ctxStub(); this._ctx._main = this.id === "cv"; }
      return this._ctx;
    },
    querySelector: function () { return el("child"); },
    querySelectorAll: function (sel) {
      // 명부(.who)는 innerHTML 에서 실제로 뽑아낸다 — 초상 렌더까지 돌려 보려고.
      if (sel !== ".who") return [];
      var out = [], re = /data-agent="([^"]+)"/g, m;
      while ((m = re.exec(this.innerHTML))) {
        out.push({ dataset: { agent: m[1] }, querySelector: function () { return el("port"); } });
      }
      return out;
    },
  };
  return e;
}

var DOM = {};
var document = {
  getElementById: function (id) { return (DOM[id] = DOM[id] || el(id)); },
};

function addEventListener() {}
var RAF = [];
function requestAnimationFrame(fn) { RAF.push(fn); return RAF.length; }
function cancelAnimationFrame() {}
var performance = { now: function () { return PERF_T; } };
var PERF_T = 0;
function setTimeout(fn) { return 0; }
function clearTimeout() {}

var STATE = __STATE__;
var TRACE = __TRACE__;
function fetch(url) {
  var body = url.indexOf("/api/trace/") === 0 ? TRACE : STATE;
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(body); } });
}

/* ── index.html 의 스크립트 본문 ─────────────────────────────────────── */
__SCRIPT__
/* ──────────────────────────────────────────────────────────────────── */

/* 프레임을 n 번 돌린다 (걷기 보간이 실제로 진행되는지 보려고). */
function pump(frames, ms) {
  for (var i = 0; i < frames; i++) {
    var q = RAF; RAF = [];
    if (!q.length) break;
    PERF_T += ms;
    for (var j = 0; j < q.length; j++) q[j](PERF_T);
  }
}

function sectorRectOf(agentId, zone) {
  var a = S.state.agents.find(function (x) { return x.agent_id === agentId; });
  var lv = S.plan.levels[levelOfAgent(a)];
  return sectorOf(lv, zone);
}

/* 시계 t 에서 모든 에이전트의 위치가 occupancy 와 맞는지 본다. */
function placementAt(t) {
  setClock(t, true);
  return S.state.agents.map(function (a) {
    var p = S.pos[a.agent_id], seg = segAt(a.agent_id, t);
    var lv = S.plan.levels[levelOfAgent(a)];
    var r = { id: a.agent_id, level: p.level, gx: p.gx, gy: p.gy, place: "",
              seg: seg ? seg.kind : null, zone: seg ? seg.zone : null, ok: true, why: "" };
    if (!seg) {
      var lg = lv.lounge;                         // 스팬이 없으면 자기 층 휴게실
      r.place = "lounge";
      r.ok = p.level === lv.level && p.gx >= lg.x && p.gx <= lg.x + lg.w &&
             p.gy >= lg.y && p.gy <= lg.y + lg.d;
      r.why = "스팬 없음 → 자기 층 휴게실 안이어야 한다";
    } else if (seg.kind === "work") {
      r.place = "sector";
      var sc = sectorRectOf(a.agent_id, seg.zone);
      r.ok = !!sc && p.level === levelOfAgent(a) &&
             p.gx >= sc.x && p.gx <= sc.x + sc.w && p.gy >= sc.y && p.gy <= sc.y + sc.d;
      r.why = "work 스팬 → 그 존 섹터 안에 있어야 한다";
    } else {
      var d = deskSlot(a);                        // 자기 자리 = 자기 팀 아일랜드
      r.place = "desk";
      r.ok = p.level === lv.level && Math.abs(p.gx - d.gx) < 0.01 && Math.abs(p.gy - d.gy) < 0.01;
      r.why = "desk 스팬 → 자기 팀 데스크에 있어야 한다";
    }
    return r;
  });
}

function runChecks() {
  var out = { views: {}, placements: {}, walk: null, roster: 0, calls: null, errors: [] };
  try {
    var w = S.state.window;

    // 1. 세 뷰가 전부 그려진다 (죽지 않는다)
    ["building", "floor", "desk"].forEach(function (v) {
      S.view = v;
      S.div = S.state.floorplan.floors[0].division_id;
      S.agent = S.state.agents[0].agent_id;
      S.spans = TRACE;
      var before = CALLS.fillRect + CALLS.poly + CALLS.text;
      draw();
      out.views[v] = CALLS.fillRect + CALLS.poly + CALLS.text - before;
    });
    S.view = "building";

    // 2. 배치 — 기록 구간의 여러 시점
    var pts = [w.start_ns, w.start_ns + (w.end_ns - w.start_ns) / 3,
               w.start_ns + (w.end_ns - w.start_ns) * 2 / 3, w.end_ns - 1];
    pts.forEach(function (t, i) { out.placements["t" + i] = placementAt(t); });

    // 3. 아무 업무도 없는 시각(기록 구간 이전) → 전원 대기실
    out.placements.before = placementAt(w.start_ns - 3600e9);

    // 4. 걷기 — 목표가 바뀌면 실제로 이동하고, 안 바뀌면 한 픽셀도 안 움직인다
    setClock(w.start_ns, true);
    var moverId = null, first = null;
    for (var i = 0; i < S.state.occupancy.length; i++) {
      var s = S.state.occupancy[i];
      if (s.kind === "work") { moverId = s.agent_id; first = s; break; }
    }
    var p0 = S.pos[moverId];
    var from = { gx: p0.gx, gy: p0.gy, level: p0.level };
    setClock((first.start_ns + first.end_ns) / 2, false);
    pump(400, 33);
    var p1 = S.pos[moverId];
    var still = S.state.agents.filter(function (a) { return !segAt(a.agent_id, S.clock); })[0];
    var q0 = still ? { gx: S.pos[still.agent_id].gx, gy: S.pos[still.agent_id].gy } : null;
    pump(60, 33);
    var q1 = still ? { gx: S.pos[still.agent_id].gx, gy: S.pos[still.agent_id].gy } : null;
    var sc = sectorRectOf(moverId, first.zone);
    out.walk = {
      agent: moverId, zone: first.zone,
      moved: Math.abs(p1.gx - from.gx) + Math.abs(p1.gy - from.gy),
      arrived: Math.abs(p1.gx - p1.tgt.gx) + Math.abs(p1.gy - p1.tgt.gy),
      inSector: !!sc && p1.gx >= sc.x && p1.gx <= sc.x + sc.w &&
                p1.gy >= sc.y && p1.gy <= sc.y + sc.d,
      // 근무 자리와 자기 자리가 같으면 "섹터에 들어갔다"는 표현이 성립하지 않는다
      awayFromDesk: (function () {
        var d = deskSlot(S.state.agents.find(function (a) { return a.agent_id === moverId; }));
        return Math.abs(p1.gx - d.gx) + Math.abs(p1.gy - d.gy);
      })(),
      idleDrift: q0 && q1 ? Math.abs(q1.gx - q0.gx) + Math.abs(q1.gy - q0.gy) : 0,
    };

    // 5. 명부 초상이 실제로 그려진다
    out.roster = DOM.side.querySelectorAll(".who").length;

    // 6. 히트 판정 — 사람과 섹터를 실제로 집을 수 있나
    S.view = "building"; draw();
    out.hits = { total: S.hit.length,
                 people: S.hit.filter(function (h) { return !!h.circle; }).length };
  } catch (e) {
    out.errors.push(String(e) + " @ " + (e.stack || ""));
  }
  out.calls = { fillRect: CALLS.fillRect, poly: CALLS.poly, text: CALLS.text, warn: CALLS.warn };
  return JSON.stringify(out);
}
