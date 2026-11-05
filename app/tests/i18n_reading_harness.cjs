// Small translation contract for module tests; the DOM engine has its own tests.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
module.exports = function i18nReading(locale = 'zh-CN') {
  const catalog = {}, listeners = [];
  const api = {
    register: entries => Object.assign(catalog, entries),
    getLocale: () => locale,
    onChange: callback => listeners.push(callback),
    t(key, params = {}) {
      const value = locale === 'en' ? catalog[key] ?? key : key;
      return String(typeof value === 'function' ? value(params) : value).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? '{' + name + '}'));
    },
    setLocale(next) { locale = next; listeners.forEach(callback => callback(next)); },
    applyDom(root) {
      for (const node of root.querySelectorAll('[data-i18n]')) node.textContent = api.t(node.dataset.i18n, JSON.parse(node.dataset.i18nParams || '{}'));
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/i18n-reading.js'), 'utf8'), { window: { BioI18n: api } });
  return api;
};
