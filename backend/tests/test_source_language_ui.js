// Original-language controls, payloads and measured timing; no service calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root,'app.js'),'utf8');
const registry = fs.readFileSync(path.join(root,'source_language.js'),'utf8');
function fn(name) {
    const start=app.lastIndexOf('function '+name+'(');
    assert.ok(start>=0,name);
    let at=app.indexOf('{',start), depth=1, end=at+1;
    for (;depth;end++) { if(app[end]==='{')depth++; if(app[end]==='}')depth--; }
    return app.slice(start,end);
}
function setup() {
    const elements=new Map();
    function element(tag) {
        return {tag,children:[],style:{},value:'',textContent:'',append(...items){this.children.push(...items);},
            appendChild(item){this.children.push(item);},prepend(item){this.children.unshift(item);},
            replaceChildren(){this.children=[];},set id(id){this._id=id;elements.set(id,this);},get id(){return this._id;}};
    }
    const doc={getElementById:id=>elements.get(id)||null,createElement:element};
    const context=vm.createContext({window:{currentLang:'en'},document:doc,currentSourceLanguage:'en',currentJobId:'invented',
        segmentsData:[],speakerGenderByName:{},speakerGenderAuto:{},speakerRolesByName:{},
        explicitGender:()=>false,ENGLISH_CHARS_PER_SEC:15});
    vm.runInContext(registry,context);context.LisanSource=context.window.LisanSource;
    return {context,elements,element};
}
test('all eight original languages have consistent neutral English and Arabic names',()=>{
    const {context:c}=setup();
    assert.deepEqual(Array.from(c.LisanSource.entries,e=>e.code),['en','es','fr','pt','it','de','nl','pl']);
    for(const entry of c.LisanSource.entries){
        assert.equal(c.LisanSource.name(entry.code,'en'),entry.en);
        assert.equal(c.LisanSource.name(entry.code,'ar'),entry.ar);
        assert.match(entry.ar,/[\u0600-\u06ff]/);
        assert.doesNotMatch(entry.en,/beta|model|vendor/i);
    }
});
test('missing old and malformed source settings default to English',()=>{
    const {context:c}=setup();
    for(const value of [undefined,null,'','xx',{},123])assert.equal(c.LisanSource.normalise(value),'en');
    assert.equal(c.LisanSource.normalise(' FR '),'fr');
});
test('changing the interface language keeps the project choice and mounts only one picker',()=>{
    const {context:c,elements,element}=setup();const target=element('div');
    c.LisanSource.mount(target,'en');elements.get('sourceLanguage').value='de';
    c.LisanSource.mount(target,'ar');
    assert.equal(target.children.length,1);
    assert.equal(elements.get('sourceLanguage').value,'de');
    assert.equal(elements.get('sourceLanguage').children.length,8);
    assert.equal(elements.get('sourceLanguageLabel').textContent,'لغة الفيديو الأصلية');
    c.LisanSource.mount(target,'en');
    assert.equal(elements.get('sourceLanguage').value,'de');
});
test('the original column returns to English after a foreign project',()=>{
    const {context:c,element}=setup();const heading=element('th');heading.id='thEnglish';
    vm.runInContext(fn('sourceControls'),c);
    c.currentSourceLanguage='fr';c.sourceControls();assert.equal(heading.textContent,'French');
    c.currentSourceLanguage='en';c.sourceControls();assert.equal(heading.textContent,'English');
    c.window.currentLang='ar';c.sourceControls();assert.equal(heading.textContent,'الإنجليزية');
});
test('the short-dub picker mounts outside the upload control and keeps its choice on refresh',()=>{
    const {context:c,elements,element}=setup();
    const host=element('div');host.id='sourceLanguageSection';
    const button=element('button');
    const file=element('input');file.id='audioFile';file.parentElement=button;
    vm.runInContext(fn('sourceControls'),c);
    c.sourceControls();
    assert.equal(host.children.length,1);
    assert.equal(button.children.length,0);
    elements.get('sourceLanguage').value='fr';
    c.window.currentLang='ar';c.sourceControls();
    assert.equal(host.children.length,1);
    assert.equal(button.children.length,0);
    assert.equal(elements.get('sourceLanguage').value,'fr');
    assert.equal(elements.get('sourceLanguageLabel').textContent,'لغة الفيديو الأصلية');
});
test('translation payload carries project source without changing original or Arabic text',()=>{
    const {context:c}=setup();vm.runInContext(fn('translateBody'),c);
    c.currentSourceLanguage='it';const rows=[{segment_id:'a',speaker:'A',text:'Buongiorno.',arabic_text:'صباح الخير.'}];
    const before=JSON.stringify(rows);const body=c.translateBody(rows,'offline');
    assert.equal(body.source_language,'it');assert.equal(body.job_id,'invented');
    assert.equal(JSON.stringify(rows),before);
});
test('foreign original timing uses measured slots and keeps the English timing calculation',()=>{
    const {context:c}=setup();vm.runInContext(fn('sourceLineDuration'),c);
    const row={text:'123456789012345',start:4,end:9};
    assert.equal(c.sourceLineDuration(row),1);
    for(const code of ['es','fr','pt','it','de','nl','pl']){
        c.currentSourceLanguage=code;assert.equal(c.sourceLineDuration(row),5);
    }
});
