const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const review = require(path.join(process.env.LISAN_BACKEND, 'background_review.js'));
const report = {ambience_scenes:[{start:0,end:60,filled_sec:58,replacement:true,asset_id:'<script>vendor</script>'},{start:60,end:75,filled_sec:0,review:true}],gaps:[{start:80,end:90,ok:true}]};
test('English report distinguishes replacements, unresolved ambience and restored music',()=>{
  const html=review.html(report,'en');
  assert.match(html,/Replacement ambience/); assert.match(html,/Check background/); assert.match(html,/Music restored/);
  assert.match(html,/0:00–1:00/); assert.match(html,/Ambience matching is free/); assert.doesNotMatch(html,/vendor|script/);
});
test('Arabic report is Arabic with left-to-right times',()=>{
  const html=review.html(report,'ar'); assert.match(html,/تفاصيل الخلفية/); assert.match(html,/أجواء بديلة/);
  assert.match(html,/dir="rtl"/); assert.match(html,/dir="ltr"/); assert.doesNotMatch(html,/Replacement|Background/);
});
test('missing and invalid reports do not show fabricated outcomes',()=>{
  assert.equal(review.html(null,'en'),''); assert.equal(review.html({},'en'),'');
  assert.equal(review.html({ambience_scenes:[{start:NaN,end:10},{start:5,end:4},null]},'en'),'');
});
