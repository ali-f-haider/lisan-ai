'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function editor(initial = []) {
  let server = {ranges: initial, revision: 'r0', duration: 10}, opened, calls = [], error, saves = 0;
  const player = {isOpen: () => false, open: opts => { opened = opts; }};
  const c = vm.createContext({window: {LisanPlayer: player}, LisanPlayer: player,
    document: {createElement: () => ({})},
    fetch: async (url, opts) => {
      calls.push({url, opts});
      if (opts.method === 'PUT') {
        const body = JSON.parse(opts.body);
        if (body.revision !== server.revision) return {ok: false, json: async () => ({error: 'Saved speech sections changed.'})};
        server = {...server, ranges: body.ranges, revision: 'r' + (saves++ + 1)};
      }
      return {ok: true, json: async () => JSON.parse(JSON.stringify(server))};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../speech_removal.js'), 'utf8'), c);
  const service = c.window.LisanSpeechRemoval;
  return {service, calls, player, get opened() {return opened;}, get server() {return server;},
    stale() {server.revision = 'other';}, get error() {return error;},
    async open(extra = {}) {await service.open({job: 'j', endpoint: '/regions',
      player: {start: 2, end: 3, onApply: () => {throw Error('ordinary timing handler called');}},
      error: ex => {error = ex;}, ...extra});}};
}
const plain = x => JSON.parse(JSON.stringify(x));

test('a removal replaces only the removal handler and keeps Arabic timing unchanged', async () => {
  const e = editor(); let notifications = 0;
  await e.open({saved: () => notifications++});
  assert.equal(e.opened.purpose, 'speech-removal');
  await e.opened.onApply(4, 5);
  assert.deepEqual(e.server.ranges, [[4, 5]]);
  assert.equal(e.opened.start, 2); assert.equal(e.opened.end, 3);
  assert.equal(notifications, 1);
  assert.equal(e.calls[1].opts.credentials, 'same-origin');
});
test('overlapping regions can be undone individually', async () => {
  const e = editor([[1, 3], [2, 4]]); await e.open();
  const saved = await e.opened.onUndo(0);
  assert.deepEqual(plain(saved), [[2, 4]]);
  assert.deepEqual(e.server.ranges, [[2, 4]]);
});
test('a stale tab never overwrites another save', async () => {
  const e = editor([[1, 2]]); await e.open(); e.stale();
  await assert.rejects(e.opened.onApply(3, 4), /changed/);
  assert.deepEqual(e.server.ranges, [[1, 2]]);
  assert.deepEqual(plain(e.service.saved('j')), [[1, 2]]);
});
test('busy or changed projects do not save', async () => {
  const e = editor(); await e.open({busy: () => true}); assert.equal(e.calls.length, 0);
  let current = true; await e.open({current: () => current}); current = false;
  assert.equal(await e.opened.onApply(3, 4), false); assert.equal(e.calls.length, 1);
});
test('a project change while saving never updates the new editor', async () => {
  const e = editor();let current=true, notices=0;
  await e.open({current:()=>current,saved:()=>notices++});
  const save=e.opened.onApply(4,5);current=false;await save;
  assert.deepEqual(e.server.ranges,[[4,5]]);assert.equal(notices,0);
});
test('an Arabic save failure stays in Arabic', async () => {
  const e=editor();await e.open({player:{lang:'ar',start:2,end:3}});e.stale();
  await assert.rejects(e.opened.onApply(4,5), /أعد فتح المشغل/);
});
test('a project snapshot round trips without mutating its saved regions', async () => {
  const e = editor(); const regions = [[1, 2]]; e.service.remember('j', regions);
  regions[0][0] = 7;
  assert.deepEqual(plain(e.service.saved('j')), [[1, 2]]);
  await e.service.sync('/regions', 'j');
  assert.deepEqual(e.server.ranges, [[1, 2]]);
  const copy = e.service.saved('j'); copy[0][0] = 9;
  assert.deepEqual(plain(e.service.saved('j')), [[1, 2]]);
  await e.service.sync('/regions', 'j');
  assert.equal(e.calls.filter(c => c.opts.method === 'PUT').length, 1);
});
test('opening an old project never clears server regions', async () => {
  const e = editor([[4, 5]]);
  assert.equal(e.service.projectData('j'), undefined, 'unknown server state must not become an imported empty list');
  await e.service.sync('/regions', 'j');
  assert.equal(e.calls.filter(c => c.opts.method === 'PUT').length, 0);
  assert.deepEqual(plain(e.service.saved('j')), [[4, 5]]);
  assert.deepEqual(plain(e.service.projectData('j')), [[4, 5]]);
});
test('invalid imported regions are rejected before any request', () => {
  const e = editor();
  for (const ranges of [null, [[NaN, 2]], [[-1, 2]], [[1, 1]], [[true, 2]], [['1', 2]]]) {
    assert.throws(() => e.service.remember('j', ranges), /Invalid/);
  }
  assert.equal(e.calls.length, 0);
});
test('both languages use a short button and a separate tooltip', () => {
  const e = editor(), children = [], parent = {appendChild: b => children.push(b)};
  e.service.button(parent, () => {}, 'en', 'action-btn');
  e.service.button(parent, () => {}, 'ar');
  assert.equal(children[0].textContent, 'Remove speech');
  assert.equal(children[1].textContent, 'إزالة الكلام');
  assert.equal(children[0].className, 'action-btn');
  assert.match(children[0].title, /original speech/);
});

// Minimal browser objects exercise the real dialog handlers without a browser install.
function dialog() {
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.ids = {}; this.listeners = {};
      this.duration = 10; this.currentTime = 0; this.paused = true;
      const classes = new Set(); this.classList = {add: s=>classes.add(s), remove: s=>classes.delete(s),
        toggle: (s, force) => {if(force===undefined)force=!classes.has(s);if(force)classes.add(s);else classes.delete(s);}, contains:s=>classes.has(s)};
    }
    set innerHTML(html) {for(const m of html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"/g)) this.ids[m[2]] = new Element(m[1]);}
    querySelector(s) {return this.ids[s.slice(1)] || null;}
    querySelectorAll() {return [];}
    appendChild(el) {el.parentNode=this;this.children.push(el);return el;}
    removeChild(el) {this.children.splice(this.children.indexOf(el),1);el.parentNode=null;}
    replaceChildren() {this.children=[];}
    setAttribute() {}
    removeAttribute() {}
    addEventListener(k, fn) {(this.listeners[k] ||= []).push(fn);}
    removeEventListener(k, fn) {this.listeners[k] = (this.listeners[k] || []).filter(f=>f!==fn);}
    emit(k, event = {}) {for(const fn of this.listeners[k] || [])fn(event);}
    load() {}
    pause() {this.paused=true;}
  }
  const doc = new Element('document');doc.body = new Element('body');doc.createElement = tag => new Element(tag);
  const c = vm.createContext({window:{}, document:doc, setTimeout,clearTimeout,requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../longdub_player.js'),'utf8'),c);
  const player=c.window.LisanPlayer;
  return {player,doc,async open(opts = {}) {
    const st=player.open({purpose:'speech-removal',lang:'en',start:2,end:3,duration:10,local:{url:'tone',kind:'audio'},...opts});
    await new Promise(setImmediate);st.el.emit('loadedmetadata');return st;
  }, escape(){doc.emit('keydown',{key:'Escape',preventDefault(){},stopPropagation(){}});}};
}
const settle = () => new Promise(setImmediate);

test('Escape and Cancel never save a speech region', async () => {
  const d=dialog();let saves=0;
  await d.open({onApply:()=>saves++});d.escape();assert.equal(d.player.isOpen(),false);
  const st=await d.open({onApply:()=>saves++});st.q('plDiscard').onclick();
  assert.equal(d.player.isOpen(),false);assert.equal(saves,0);
});
test('Remove saves an unchanged selection only when explicitly clicked', async () => {
  const d=dialog();const calls=[];
  const st=await d.open({onApply:(a,b)=>{calls.push([a,b]);return true;}});
  st.q('plClose').onclick();await settle();assert.deepEqual(calls,[[2,3]]);assert.equal(d.player.isOpen(),false);
});
test('ordinary timing Escape preserves its previous save behavior', async () => {
  const d=dialog();const calls=[];
  const st=await d.open({purpose:'timing',onApply:(a,b)=>{calls.push([a,b]);return true;}});
  st.q('plA').value='0:01.500';st.q('plA').onchange();d.escape();await settle();
  assert.deepEqual(calls,[[1.5,3]]);assert.equal(d.player.isOpen(),false);
});
test('invalid times and failed saves keep the dialog open', async () => {
  const d=dialog();let saves=0;
  const st=await d.open({onApply:()=>{saves++;throw Error('Reopen the player.');}});
  st.q('plB').value='bad';st.q('plB').onchange();st.q('plClose').onclick();await settle();
  assert.equal(saves,0);assert.equal(st.q('plB').value,'bad');
  st.q('plB').value='11';st.q('plB').onchange();st.q('plClose').onclick();await settle();
  assert.equal(saves,0);assert.equal(d.player.isOpen(),true);
  st.q('plB').value='3';st.q('plB').onchange();st.q('plClose').onclick();await settle();
  assert.equal(saves,1);assert.equal(d.player.isOpen(),true);assert.equal(st.q('plClose').disabled,false);
  assert.equal(st.q('plErr').textContent,'Reopen the player.');
});
test('Remove uses the typed marks even before a change event arrives', async()=>{
  const d=dialog(), calls=[];
  const st=await d.open({onApply:(a,b)=>{calls.push([a,b]);return true;}});
  st.q('plA').value='4';st.q('plB').value='5';st.q('plClose').onclick();await settle();
  assert.deepEqual(calls,[[4,5]]);
});
test('undo persists the clicked region and does not apply the timing selection', async () => {
  const d=dialog();const calls=[];
  const st=await d.open({savedRanges:[[1,2],[3,4]],onApply:()=>{throw Error('timing saved');},
    onUndo:async index=>{calls.push(index);return [[3,4]];}});
  st.q('plSaved').children[0].onclick();await settle();
  assert.deepEqual(calls,[0]);assert.equal(st.q('plSaved').children.length,1);assert.equal(d.player.isOpen(),true);
  assert.equal(st.q('plDiscard').disabled,false);
});

function exportButton(price) {
  const controls={error:{textContent:''},dub:{textContent:''},selectedCount:{textContent:''}};
  let quote={max_total:price,token:'q1'}, exports=0, notices=0, dialogs=0;
  const c=vm.createContext({working:false,selected:new Set(),project:{id:'j1'},
    state:{busy:false,speech_removal_configured:true},backgroundPrice:null,
    $:id=>controls[id],tr:en=>en,setBusy:()=>{},save:async()=>{},
    api:async path=>{if(path.endsWith('/quote'))return quote;if(path.endsWith('/dub')){exports++;return {}; }throw Error('unexpected endpoint');},
    progress:()=>{},poll:async()=>{c.state.busy=false;},refreshCredits:async()=>{},
    error:ex=>{throw ex;},notify:()=>notices++,LisanDialog:{confirm:async()=>{dialogs++;return true;}}});
  const js=fs.readFileSync(path.join(__dirname,'../dub_long_edit.js'),'utf8');
  vm.runInContext(js.slice(js.indexOf('  function count()'),js.indexOf('  function invalidate(')),c);
  vm.runInContext(js.slice(js.indexOf("  $('dub').onclick=async function(){"),js.indexOf("  $('finish').onclick=")),c);
  return {click:()=>controls.dub.onclick(),get exports(){return exports;},get notices(){return notices;},
    get dialogs(){return dialogs;},get label(){return controls.dub.textContent;},
    changePrice(max){quote={max_total:max,token:'q2'};}};
}
test('a zero-credit background export starts directly without a dialog', async()=>{
  const e=exportButton(0);await e.click();assert.equal(e.exports,1);assert.equal(e.dialogs,0);
});
test('a paid background repair displays its price before it starts', async()=>{
  const e=exportButton(10);await e.click();assert.equal(e.exports,0);assert.match(e.label,/10 credits/);
  await e.click();assert.equal(e.exports,1);assert.equal(e.dialogs,0);
  e.changePrice(20);await e.click();assert.equal(e.exports,1);assert.match(e.label,/20 credits/);
});
