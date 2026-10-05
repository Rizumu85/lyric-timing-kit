import { WEBBRIDGE } from './kit-config.mjs';
// Tiny client for the Kimi WebBridge daemon (the user's own Chrome): one command per call.
// import { wb } from './wb.mjs';  await wb('navigate', { url }, 'session-name')
export async function wb(action, args = {}, session = 'lyrics-readings') {
  const response = await fetch(WEBBRIDGE + '/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, args, session }) });
  const reply = await response.json();
  if (!reply.ok) throw new Error(`${action}: ${reply.error?.code} ${reply.error?.message?.slice(0, 200)}`);
  return reply.data;
}
export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
// Run an expression in the page and return its value (strings come back as they are).
export async function evaluate(code, session) {
  const data = await wb('evaluate', { code }, session);
  return data.value;
}
