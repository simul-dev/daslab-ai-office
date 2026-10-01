import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/office.js', import.meta.url), 'utf8');

function officeFixture() {
  const elements = new Map();
  const requests = [];
  let voice;
  function element(id) {
    if (!elements.has(id)) {
      const listeners = new Map(), attributes = new Map(), classes = new Set();
      elements.set(id, {
        id, value: '', textContent: '', focusCount: 0,
        classList: {
          contains: name => classes.has(name),
          toggle(name, force) {
            const present = force === undefined ? !classes.has(name) : force;
            if (present) classes.add(name); else classes.delete(name);
            return present;
          },
        },
        addEventListener: (name, callback) => listeners.set(name, callback),
        click() { if (!this.disabled) listeners.get('click')?.({preventDefault() {}}); },
        focus() { this.focusCount++; },
        getAttribute: name => attributes.get(name) ?? (name === 'aria-expanded' ? 'false' : null),
        setAttribute: (name, value) => attributes.set(name, value),
      });
    }
    return elements.get(id);
  }
  class VoiceInput {
    constructor(options) {
      this.toggles = 0; this.cancels = 0; voice = this;
      options.onState({recording: false, busy: false, canStart: false, canStop: false, canCancel: false, message: '대기'});
    }
    toggle() { this.toggles++; }
    cancel() { this.cancels++; }
    setEnabled() {}
  }
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll: () => [], addEventListener() {}},
    window: {DASVoiceInput: {InlineVoiceInput: VoiceInput}, addEventListener() {}},
    sessionStorage: {getItem: () => null, removeItem() {}},
    location: {replace() {}},
    fetch: async url => { requests.push(url); throw new Error('offline test'); },
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    Intl, Date, URL,
  });
  vm.runInContext(source, context, {filename: 'office.js'});
  return {element, requests, BrowserSpeechInput: context.window.DASBrowserSpeechInput, get voice() {return voice;}};
}

function speechFixture({legacy = false, supported = true} = {}) {
  const office = officeFixture();
  const recognitions = [], timers = new Map(), states = [], transcripts = [];
  let nextTimer = 0, allowed = true;
  class Recognition {
    constructor() { this.starts = 0; this.stops = 0; this.aborts = 0; recognitions.push(this); }
    start() { this.starts++; }
    stop() { this.stops++; }
    abort() { this.aborts++; }
    final(text) { this.onresult?.({resultIndex: 0, results: [{isFinal: true, 0: {transcript: text}}]}); }
  }
  const host = {
    setTimeout: (callback, delay) => {timers.set(++nextTimer, {callback, delay}); return nextTimer;},
    clearTimeout: id => timers.delete(id),
  };
  if (supported) host[legacy ? 'webkitSpeechRecognition' : 'SpeechRecognition'] = Recognition;
  const view = {hidden: false};
  const controller = new office.BrowserSpeechInput({host, view, isAllowed: () => allowed,
    onState: value => states.push(value), onTranscript: value => transcripts.push(value.text)});
  return {controller, recognitions, timers, states, transcripts, view, setAllowed: value => {allowed = value;}};
}

test('keyboard dictation action only focuses the existing draft', () => {
  const office = officeFixture();
  const draft = office.element('command-input');
  const button = office.element('keyboard-dictation');
  assert.equal(button.disabled, true, 'the button is unavailable before authentication and staff load');
  button.disabled = false; // Simulate an authenticated, writable composer.
  draft.value = 'PM, 지금 진행 상황을 보고해줘';
  button.click();
  assert.equal(draft.focusCount, 1);
  assert.equal(draft.value, 'PM, 지금 진행 상황을 보고해줘');
  assert.equal(office.voice.toggles, 0, 'the app microphone must not start');
  assert.equal(office.voice.cancels, 0);
  assert.deepEqual(office.requests, ['/api/auth'], 'the button must not submit a mission');
});

test('mobile controls follow the draft in keyboard order', () => {
  const html = readFileSync(new URL('../static/office.html', import.meta.url), 'utf8');
  const ids = ['command-input', 'browser-speech-toggle', 'keyboard-dictation', 'local-voice-disclosure', 'voice-toggle', 'command-submit'];
  const positions = ids.map(id => html.indexOf(`id="${id}"`));
  assert.ok(positions.every(position => position >= 0));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
});

test('one browser speech tap starts Korean recognition and appends one reviewed draft', () => {
  const speech = speechFixture();
  assert.equal(speech.controller.start(), true);
  const recognition = speech.recognitions[0];
  assert.equal(recognition.starts, 1);
  assert.equal(recognition.lang, 'ko-KR');
  assert.equal(recognition.continuous, false);
  assert.equal(recognition.interimResults, false);
  assert.equal(recognition.maxAlternatives, 1);
  assert.equal([...speech.timers.values()][0].delay, 60000);
  recognition.onstart();
  assert.equal(speech.states.at(-1).recording, true);
  recognition.onresult({resultIndex: 0, results: [{isFinal: false, 0: {transcript: '미완성'}}]});
  assert.deepEqual(speech.transcripts, []);
  recognition.final(' PM, 진행 상황 보고해줘 ');
  recognition.final(' PM, 진행 상황 보고해줘 ');
  assert.deepEqual(speech.transcripts, ['PM, 진행 상황 보고해줘']);
  assert.equal(recognition.aborts, 1);
  assert.equal(speech.timers.size, 0);
  assert.equal(speech.states.at(-1).active, false);
});

test('browser dictation can be stopped, cancelled, and late results cannot reappear', () => {
  const speech = speechFixture({legacy: true});
  assert.equal(speech.controller.available, true, 'webkitSpeechRecognition is supported');
  speech.controller.start();
  const first = speech.recognitions[0];
  assert.equal(speech.controller.stop(), true);
  assert.equal(first.stops, 1);
  assert.equal(speech.states.at(-1).processing, true);
  first.final('PM, 첫 문장');
  assert.deepEqual(speech.transcripts, ['PM, 첫 문장']);
  speech.controller.start();
  const second = speech.recognitions[1];
  speech.controller.cancel();
  second.final('취소된 문장');
  second.onend();
  assert.deepEqual(speech.transcripts, ['PM, 첫 문장']);
  assert.equal(second.aborts, 1);
});

test('timeout, permission error, and unsupported browser use explicit keyboard fallback', () => {
  const speech = speechFixture();
  speech.controller.start();
  const first = speech.recognitions[0];
  [...speech.timers.values()][0].callback();
  first.final('늦은 문장');
  assert.deepEqual(speech.transcripts, []);
  assert.equal(first.aborts, 1);
  assert.match(speech.states.at(-1).message, /60초/);
  speech.controller.start();
  speech.recognitions[1].onerror({error: 'not-allowed'});
  assert.match(speech.states.at(-1).message, /키보드 마이크/);
  const unsupported = speechFixture({supported: false});
  assert.equal(unsupported.controller.available, false);
  assert.equal(unsupported.controller.start(), false);
  assert.equal(unsupported.recognitions.length, 0);
  assert.match(unsupported.states.at(-1).message, /키보드 마이크/);
});

test('browser dictation refuses to start when authorization is gone or tab is hidden', () => {
  const speech = speechFixture();
  speech.setAllowed(false);
  assert.equal(speech.controller.start(), false);
  speech.setAllowed(true);
  speech.view.hidden = true;
  assert.equal(speech.controller.start(), false);
  assert.equal(speech.recognitions.length, 0);
});

test('experimental app dictation requires a separate explicit choice', () => {
  const office = officeFixture();
  const disclosure = office.element('local-voice-disclosure');
  const panel = office.element('inline-voice');
  disclosure.click();
  assert.equal(disclosure.getAttribute('aria-expanded'), 'true');
  assert.equal(panel.classList.contains('mobile-expanded'), true);
  assert.equal(office.voice.toggles, 0);
  office.element('voice-toggle').disabled = false; // Simulate an authenticated, writable composer.
  office.element('voice-toggle').click();
  assert.equal(office.voice.toggles, 1);
  disclosure.click();
  assert.equal(disclosure.getAttribute('aria-expanded'), 'false');
  assert.equal(panel.classList.contains('mobile-expanded'), false);
  assert.equal(office.voice.cancels, 1, 'closing the option stops an in-flight app recording');
});
