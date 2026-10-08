import { useState, type FormEvent } from "react";
import { auth } from "../api";
import { Wordmark } from "../Wordmark";

export const DEMO = { email: "demo@northbeam.example", password: "northbeam-demo" };

type Mode = "login" | "signup" | "demo" | "join";

/** Sign in, start a workspace, open the demo, or accept an invitation. */
export function SignIn({ mode: initial, invitation, onDone }: { mode: Mode; invitation?: string; onDone: () => void }) {
  const [mode, setMode] = useState<Mode>(initial);
  const [form, setForm] = useState({
    name: "", email: initial === "demo" ? DEMO.email : "", password: initial === "demo" ? DEMO.password : "",
    workspace: "", practice: "",
  });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const creating = mode === "signup" || (mode === "join" && !!invitation);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (creating) {
        await auth.signup({ name: form.name, email: form.email, password: form.password,
          workspace: form.workspace || form.name, practice_description: form.practice || undefined, invitation });
      } else {
        await auth.login(form.email, form.password);
        if (invitation) await auth.join(invitation);
      }
      onDone();
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    setBusy(false);
  };

  const heading = { login: "Sign in", demo: "Open the demo practice", signup: "Start a workspace",
    join: "Join your team's workspace" }[mode];

  return (
    <div className="gate">
      <a href="/" className="gate-mark"><Wordmark /></a>
      <form className="gate-card" onSubmit={submit}>
        <h1>{heading}</h1>
        {mode === "demo" && <p className="dim">Northbeam Studio is a fictional practice with 14 past proposals and 14 RFP packs. The demo login is filled in for you.</p>}
        {mode === "signup" && <p className="dim">Your workspace starts with a standard vocabulary, markets and bid rules. You can edit them, and invite your team, once you are in.</p>}
        {mode === "join" && <p className="dim">Use the email address the invitation was sent to.</p>}
        {creating && (
          <label>Your name<input required autoComplete="name" value={form.name} onChange={set("name")} /></label>
        )}
        <label>Work email<input required type="email" autoComplete="email" value={form.email} onChange={set("email")} /></label>
        <label>Password
          <input required type="password" minLength={creating ? 10 : undefined}
                 autoComplete={creating ? "new-password" : "current-password"} value={form.password} onChange={set("password")} />
          {creating && <span className="hint">At least 10 characters.</span>}
        </label>
        {mode === "signup" && <>
          <label>Practice name<input required value={form.workspace} onChange={set("workspace")} placeholder="e.g. Halden Architects" /></label>
          <label>What the practice does <span className="optional">(optional)</span>
            <input value={form.practice} onChange={set("practice")} placeholder="e.g. a hospitality and residential architecture practice in the Gulf" />
            <span className="hint">The extraction model reads RFPs with this in mind.</span>
          </label>
        </>}
        {err && <p className="error" role="alert">{err}</p>}
        <button className="primary wide" disabled={busy}>
          {busy ? "One moment…" : creating ? (mode === "join" ? "Create account and join" : "Create workspace") : "Sign in"}
        </button>
        <p className="gate-switch">
          {mode === "join" && invitation && (creating
            ? <>Already have an account? <button type="button" className="link" onClick={() => setMode("login")}>Sign in to join</button></>
            : null)}
          {(mode === "login" || mode === "demo") && !invitation && <>New to Bidwright? <button type="button" className="link" onClick={() => setMode("signup")}>Start a workspace</button></>}
          {mode === "signup" && <>Have an account? <button type="button" className="link" onClick={() => setMode("login")}>Sign in</button></>}
        </p>
        {mode !== "demo" && !invitation && (
          <p className="gate-switch"><button type="button" className="link" onClick={() => { setMode("demo"); setForm({ ...form, ...DEMO }); }}>Open the demo practice instead</button></p>
        )}
      </form>
    </div>
  );
}
