const Module = require('module');
const path = require('path');

const originalLoad = Module._load;
const userlandPunycode = path.join(__dirname, 'node_modules', 'punycode', 'punycode.js');

Module._load = function (request, parent, isMain) {
  if (request === 'punycode') {
    return originalLoad.call(this, userlandPunycode, parent, isMain);
  }
  return originalLoad.call(this, request, parent, isMain);
};
