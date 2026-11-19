/* Where the person has been in this tab, so "back" returns to the place they came from (a chat, Decide, the
   register) instead of a fixed page. Kept per tab in sessionStorage; the browser's own back button still works. */

const KEY = "bw-trail";
const MAX = 30;

function read(): string[] {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}
function write(t: string[]) {
  try { sessionStorage.setItem(KEY, JSON.stringify(t.slice(-MAX))); } catch { /* not remembered */ }
}
const norm = (h: string) => (h === "" || h === "#" ? "#/" : h);

/** Record each page as it is opened. A step back (the browser's back button) pops instead of pushing. */
export function startTrail(): void {
  const visit = () => {
    const h = norm(location.hash), t = read();
    if (t[t.length - 1] === h) return;
    if (t[t.length - 2] === h) t.pop();
    else t.push(h);
    write(t);
  };
  visit();
  window.addEventListener("hashchange", visit);
}

/** A new chat takes its own address in place of "#/": rename that entry rather than adding one. */
export function renameCurrent(to: string): void {
  const t = read();
  if (t.length) t[t.length - 1] = to;
  write(t);
}

/** The last place visited outside the current page (and outside `area`, e.g. the same request's steps). */
export function cameFrom(area?: (h: string) => boolean): string | null {
  const t = read(), here = t[t.length - 1];
  for (let i = t.length - 2; i >= 0; i--) {
    if (t[i] !== here && !(area && area(t[i]))) return t[i];
  }
  return null;
}

export function placeName(h: string): string {
  if (h.startsWith("#/chat/") || h === "#/") return "the chat";
  if (h === "#/chats") return "Chats";
  if (h === "#/decide") return "Decide";
  if (h.startsWith("#/logic")) return "How we price";
  if (h.startsWith("#/r/")) return "the request";
  if (h === "#/requests") return "Requests";
  return "where you were";
}
