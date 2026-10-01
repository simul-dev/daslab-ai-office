// The upstream canvas's optional terminal/seat actions have no effect here.
// Paperclip state is delivered only through the office snapshot adapter.
export const transport = {
  send() {},
  onMessage() { return () => {}; },
  ready: Promise.resolve(),
  state: 'connected',
  onStateChange() { return () => {}; },
  dispose() {},
};
