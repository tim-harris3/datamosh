var root = element;
var order = [];      // timeline: [{s: source id, i: section idx}, ...] in mosh order (duplicates ok)
var gridSel = [];    // selected grid cells, flat indices into `flat`
var tlSel = [];      // selected timeline positions
var gridAnchor = 0, tlAnchor = 0;
var drag = null;     // {from:'grid', entries:[{s,i},...]} | {from:'tl', positions:[...]}
var flat = [];       // grid cells across all sources, in display order: [{s, srcIdx, i, sec}]

function srcList() { return (props.value && props.value.sources) || []; }
function fmtT(t) { return (Math.round(t * 100) / 100) + 's'; }
function el(tag, cls) { var d = document.createElement(tag); if (cls) d.className = cls; return d; }

function rebuildFlat() {
  flat = [];
  srcList().forEach(function (src, si) {
    (src.sections || []).forEach(function (sec) {
      flat.push({ s: src.id, srcIdx: si, i: sec.i, sec: sec });
    });
  });
}

function srcColor(si) { return 'hsl(' + ((si * 137) % 360) + ', 70%, 55%)'; }

function dot(si) {
  var d = el('span', 'dm-dot');
  d.style.background = srcColor(si);
  return d;
}

function findCell(sid, i) {
  for (var k = 0; k < flat.length; k++)
    if (flat[k].s === sid && flat[k].i === i) return flat[k];
  return null;
}

function renderWarn() {
  root.querySelector('.dm-warn').textContent = (props.value && props.value.warning) || '';
}

function renderGrid() {
  var grid = root.querySelector('.dm-grid');
  grid.innerHTML = '';
  var list = srcList();
  if (!flat.length) {
    var hint = el('div', 'dm-empty');
    hint.textContent = 'no keyframe sections - pick a source video (or click generate keyframes)';
    grid.appendChild(hint);
    return;
  }
  var multi = list.length > 1;
  var f = 0;
  list.forEach(function (src, si) {
    var secs = src.sections || [];
    if (!secs.length) return;
    if (multi) {
      var head = el('div', 'dm-src-head');
      head.appendChild(dot(si));
      head.appendChild(document.createTextNode(src.name + ' (' + secs.length + ' sections)'));
      grid.appendChild(head);
    }
    secs.forEach(function (sec) {
      var cell = el('div', 'dm-cell' + (gridSel.indexOf(f) >= 0 ? ' sel' : ''));
      cell.draggable = true;
      cell.dataset.f = f;
      var img = el('img'); img.src = sec.thumb; img.draggable = false;
      var cap = el('div', 'dm-cap');
      if (multi) cap.appendChild(dot(si));
      cap.appendChild(document.createTextNode(
        '#' + (sec.i + 1) + ' ' + fmtT(sec.t0) + '-' + fmtT(sec.t1)));
      cell.title = src.name + ' section ' + (sec.i + 1) + ': ' + fmtT(sec.t0) + ' - ' +
        fmtT(sec.t1) + ' (drag onto the timeline)';
      cell.appendChild(img); cell.appendChild(cap);
      grid.appendChild(cell);
      f++;
    });
  });
}

function renderTimeline() {
  var tl = root.querySelector('.dm-timeline');
  tl.innerHTML = '';
  tl.classList.remove('gap-end');
  var multi = srcList().length > 1;
  if (!order.length) {
    var hint = el('div', 'dm-empty');
    hint.textContent = 'empty - drag keyframe sections here to set the mosh order';
    tl.appendChild(hint);
    return;
  }
  order.forEach(function (o, pos) {
    var c = findCell(o.s, o.i);
    var item = el('div', 'dm-item' + (tlSel.indexOf(pos) >= 0 ? ' sel' : ''));
    item.draggable = true;
    item.dataset.pos = pos;
    if (c) { var img = el('img'); img.src = c.sec.thumb; img.draggable = false; item.appendChild(img); }
    var cap = el('div', 'dm-cap');
    if (multi && c) cap.appendChild(dot(c.srcIdx));
    cap.appendChild(document.createTextNode((pos + 1) + ': #' + (o.i + 1)));
    item.appendChild(cap);
    tl.appendChild(item);
  });
}

function sync() {
  renderTimeline();
  trigger('seq', { order: order.map(function (o) { return { s: o.s, i: o.i }; }) });
}

function applySel(sel, anchor, idx, ev) {
  if (ev.shiftKey) {
    var a = Math.min(anchor.v, idx), b = Math.max(anchor.v, idx), out = [];
    for (var k = a; k <= b; k++) out.push(k);
    return out;
  }
  anchor.v = idx;
  if (ev.ctrlKey || ev.metaKey) {
    var copy = sel.slice(), p = copy.indexOf(idx);
    if (p >= 0) copy.splice(p, 1); else copy.push(idx);
    return copy;
  }
  return (sel.length === 1 && sel[0] === idx) ? [] : [idx];
}

root.addEventListener('click', function (e) {
  if (e.target.closest('.dm-clear')) { order = []; tlSel = []; sync(); return; }
  var cell = e.target.closest('.dm-cell');
  if (cell) {
    var a = { v: gridAnchor };
    gridSel = applySel(gridSel, a, +cell.dataset.f, e);
    gridAnchor = a.v;
    renderGrid();
    return;
  }
  var item = e.target.closest('.dm-item');
  if (item) {
    var b = { v: tlAnchor };
    tlSel = applySel(tlSel, b, +item.dataset.pos, e);
    tlAnchor = b.v;
    renderTimeline();
  }
});

root.addEventListener('dblclick', function (e) {
  var item = e.target.closest('.dm-item');
  if (!item) return;
  order.splice(+item.dataset.pos, 1);
  tlSel = [];
  sync();
});

root.addEventListener('dragstart', function (e) {
  var cell = e.target.closest('.dm-cell');
  var item = e.target.closest('.dm-item');
  if (cell) {
    var f = +cell.dataset.f;
    var sel = gridSel.indexOf(f) >= 0 ? gridSel.slice().sort(function (x, y) { return x - y; }) : [f];
    drag = { from: 'grid', entries: sel.map(function (k) { return { s: flat[k].s, i: flat[k].i }; }) };
  } else if (item) {
    var pos = +item.dataset.pos;
    var psel = tlSel.indexOf(pos) >= 0 ? tlSel.slice().sort(function (x, y) { return x - y; }) : [pos];
    drag = { from: 'tl', positions: psel };
  } else { return; }
  e.dataTransfer.setData('text/plain', 'dm');
  e.dataTransfer.effectAllowed = 'copyMove';
});

function gapAt(e) {
  var items = root.querySelectorAll('.dm-timeline .dm-item');
  for (var k = 0; k < items.length; k++) {
    var r = items[k].getBoundingClientRect();
    if (e.clientX < r.left + r.width / 2) return k;
  }
  return order.length;
}

function markGap(g) {
  var tl = root.querySelector('.dm-timeline');
  tl.classList.toggle('gap-end', g >= order.length && order.length > 0);
  root.querySelectorAll('.dm-timeline .dm-item').forEach(function (it, k) {
    it.classList.toggle('gapmark', k === g);
  });
}

root.addEventListener('dragover', function (e) {
  if (!drag) return;
  if (!e.target.closest('.dm-timeline')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = drag.from === 'grid' ? 'copy' : 'move';
  markGap(gapAt(e));
});

root.addEventListener('dragleave', function (e) {
  var tl = e.target.closest('.dm-timeline');
  if (tl && !(e.relatedTarget && tl.contains(e.relatedTarget))) markGap(-1);
});

root.addEventListener('drop', function (e) {
  var tl = e.target.closest('.dm-timeline');
  if (!tl || !drag) return;
  e.preventDefault();
  var g = gapAt(e);
  if (drag.from === 'grid') {
    Array.prototype.splice.apply(order, [g, 0].concat(drag.entries));
  } else {
    var moving = drag.positions.map(function (p) { return order[p]; });
    var adj = g;
    drag.positions.slice().reverse().forEach(function (p) {
      order.splice(p, 1);
      if (p < adj) adj--;
    });
    Array.prototype.splice.apply(order, [adj, 0].concat(moving));
  }
  tlSel = [];
  drag = null;
  sync();
});

root.addEventListener('dragend', function () { drag = null; markGap(-1); });

watch('value', function () {
  rebuildFlat();
  gridSel = []; tlSel = []; drag = null;
  // keep timeline entries whose (source, section) still exists, so adding or
  // removing one source doesn't wipe an arrangement built from the others
  order = order.filter(function (o) { return findCell(o.s, o.i); });
  renderWarn(); renderGrid();
  sync();
});

rebuildFlat(); renderWarn(); renderGrid(); renderTimeline();
