const test = require('node:test'), assert = require('node:assert/strict'), fs=require('fs'), path=require('path'), vm=require('vm');
const source=fs.readFileSync(path.join(__dirname,'../admin_voice_controls.js'),'utf8');
const controls=require('../admin_voice_controls.js');
test('long-dub retention text uses the configured period in both languages',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../dub_long.html'),'utf8');
  const code=html.slice(html.indexOf('function voiceRetentionText(s)'),html.indexOf('function esc(s)'));
  for(const [lang,days,expected] of [['en',2,'2 days'],['en',5,'5 days'],['ar',2,'يومين'],['ar',5,'5 أيام']]){
    const context={LANG:lang,cfg:{voice_retention_days:days},cur:null};vm.runInNewContext(code,context);
    assert.equal(context.voiceRetentionText('{voice_period}'),expected);
  }
});
test('inventory escapes names and IDs and disables protected voices',()=>{
  const html=controls.table({voices:[{id:'"onclick="bad',name:'<script>bad</script>',category:'active',selectable:false,projects:[{name:'<img>',expires_at:null}],saved_rows:0}],cleanup_busy:false});
  assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));assert(html.includes('&quot;'));assert(html.includes(' disabled'));
});
test('running work disables even normally selectable inventory rows',()=>{
  const html=controls.table({voices:[{id:'v',name:'Voice',category:'saved',selectable:true,projects:[],saved_rows:1}],cleanup_busy:true});
  assert(html.includes(' disabled'));assert(html.includes('Saved library'));
});
test('cancelled cleanup makes only inventory and preview requests',async()=>{
  const calls=[], nodes={}; const data={total:1,counts:{expired:1},retention_days:2,cleanup_busy:false,pending_cleanup:[],voices:[{id:'v',name:'Voice',category:'expired',selectable:true,projects:[],saved_rows:0}]};
  const context={window:{},module:{exports:{}},console,Date,Number,document:{getElementById:id=>nodes[id]|| (nodes[id]={innerHTML:'',textContent:'',value:''})},toast:()=>{},LisanDialog:{confirm:async()=>false},api:async(url,opts)=>{
    calls.push(url);return {ok:true,data:url.endsWith('/preview')?{token:'a'.repeat(32),count:1,voices:data.voices}:data};
  }};
  vm.runInNewContext(source,context);
  await context.window.refreshVoiceStorage();await context.window.previewVoiceCleanup(true);
  assert.deepEqual(calls,['/api/admin/voice_management/inventory','/api/admin/voice_management/preview']);
});
test('a refused settings save never announces success',async()=>{
  const messages=[];
  const context={window:{},module:{exports:{}},document:{getElementById:()=>({value:'2'})},toast:(s,error)=>messages.push([s,error]),api:async()=>({ok:false,data:{error:'not saved'}})};
  vm.runInNewContext(source,context);await context.window.saveVoiceStorageSettings();
  assert.deepEqual(messages,[['not saved',true]]);
});
