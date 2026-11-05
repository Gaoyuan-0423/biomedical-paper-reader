const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const i18nReading=require('./i18n_reading_harness.cjs');
test('export themes follow UI language without changing exported note content',()=>{
 const i18n=i18nReading(),window={BioI18n:i18n};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../frontend/export.js'),'utf8'),{window});
 const html='<h2>论文问答</h2><p>Original notes and 原文来源</p>',before=window.WechatExport.wrapDocument(html,'论文标题');
 assert.equal(window.WechatExport.themes[0].name,'经典蓝');
 i18n.setLocale('en');assert.equal(window.WechatExport.themes[0].name,'Classic blue');assert.equal(window.WechatExport.themes[0].labelKey,'经典蓝');
 assert.equal(window.WechatExport.wrapDocument(html,'论文标题'),before);
});
