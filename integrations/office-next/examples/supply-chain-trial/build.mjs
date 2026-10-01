import {readFileSync, writeFileSync} from 'node:fs';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
export function build() {
  const source = read('calculate.mjs').replace('export function calculate', 'function calculate');
  const input = JSON.stringify(JSON.parse(read('input.json'))).replaceAll('<', '\\u003c');
  return read('template.html').replace('/* CALCULATION */', source).replace('/* INITIAL_INPUT */', input);
}
writeFileSync(new URL('index.html', import.meta.url), build());
console.log('Generated supply-chain-trial/index.html from calculate.mjs + template.html + input.json');
