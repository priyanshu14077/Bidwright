import { useCallback, useContext, useEffect, useState } from "react";
import { auth, type Members as MembersData, type Role } from "../api";
import { formatDate } from "../format";
import { Session, useCan } from "../session";

const ROLE: Record<Role, string> = { owner: "Owner", admin: "Admin", estimator: "Estimator", viewer: "Viewer" };
const ROLE_CAN: Record<Role, string> = {
  owner: "Everything, including appointing owners and admins",
  admin: "Invite people, edit reference data and bid rules, run backtests",
  estimator: "Upload RFP packs, correct values, resolve conflicts, confirm records",
  viewer: "Read every record, source and backtest",
};

export function Members() {
  const me = useContext(Session);
  const can = useCan();
  const [data, setData] = useState<MembersData | null>(null);
  const [invite, setInvite] = useState<{ email: string; role: Role }>({ email: "", role: "estimator" });
  const [link, setLink] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => auth.members().then(setData), []);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); await load(); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  };
  const sendInvite = () => act(async () => {
    const r = await auth.invite(invite.email, invite.role);
    setLink(`${location.origin}${r.link}`);
    setInvite({ ...invite, email: "" });
  });

  const manage = can("members");
  const isOwner = me?.role === "owner";
  const assignable: Role[] = isOwner ? ["owner", "admin", "estimator", "viewer"] : ["estimator", "viewer"];

  if (!data) return <div className="page"><p className="empty">Loading…</p></div>;
  return (
    <div className="page admin">
      <h1>Members</h1>
      <p className="lede">Everyone here can see this workspace's records, sources and archive. Nobody outside it can.</p>
      {err && <p className="error" role="alert">{err}</p>}
      <div className="cols">
        <section className="group">
          <h2>People ({data.members.length})</h2>
          <table>
            <thead><tr><th>Name</th><th>Role</th><th>Last signed in</th>{manage && <th />}</tr></thead>
            <tbody>{data.members.map((m) => {
              const self = m.user_id === me?.user.user_id;
              const editable = manage && !self && (isOwner || !["owner", "admin"].includes(m.role));
              return (
                <tr key={m.user_id}>
                  <td><strong>{m.name}</strong>{self && <span className="dim"> (you)</span>}<br /><span className="dim">{m.email}</span></td>
                  <td>{editable ? (
                    <select aria-label={`Role for ${m.name}`} value={m.role}
                            onChange={(e) => act(() => auth.changeRole(m.user_id, e.target.value as Role))}>
                      {assignable.map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}
                    </select>) : ROLE[m.role]}</td>
                  <td>{m.last_login_at ? formatDate(m.last_login_at) : <span className="dim">Never</span>}</td>
                  {manage && <td>{editable && <button className="quiet" onClick={() => act(() => auth.remove(m.user_id))}>Remove</button>}</td>}
                </tr>);
            })}</tbody>
          </table>
        </section>
        <section className="group">
          {manage ? <>
            <h2>Invite someone</h2>
            <div className="form">
              <label>Email<input type="email" value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} placeholder="name@practice.com" /></label>
              <label>Role<select value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value as Role })}>
                {assignable.filter((r) => r !== "owner").map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}</select>
                <span className="hint">{ROLE_CAN[invite.role]}</span></label>
              <button className="primary" disabled={!invite.email.includes("@")} onClick={sendInvite}>Create invitation</button>
              {link && <div className="invite-link" role="status">
                <p>Send this link to them. It works once, for that email address, for 7 days.</p>
                <input readOnly value={link} onFocus={(e) => e.target.select()} aria-label="Invitation link" />
              </div>}
            </div>
            {data.invitations.length > 0 && <>
              <h2 className="spaced">Waiting to join</h2>
              <table><thead><tr><th>Email</th><th>Role</th><th>Expires</th></tr></thead>
                <tbody>{data.invitations.map((i) => <tr key={i.email + i.created_at}><td>{i.email}</td><td>{ROLE[i.role]}</td><td>{formatDate(i.expires_at)}</td></tr>)}</tbody></table>
            </>}
          </> : <>
            <h2>Roles</h2>
            <table><tbody>{(Object.keys(ROLE) as Role[]).map((r) => <tr key={r}><td><strong>{ROLE[r]}</strong></td><td>{ROLE_CAN[r]}</td></tr>)}</tbody></table>
          </>}
        </section>
      </div>
    </div>
  );
}
