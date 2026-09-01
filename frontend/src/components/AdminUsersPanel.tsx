// Admin modal — Users tab (platform-admin console). A dense user table (email,
// name, admin flag, enabled flag) whose two toggles dispatch straight to the
// host's onSetUser (which calls the standalone arbor.admin.set_user endpoint
// then refreshes). The viewer's OWN row renders its toggles disabled — the
// server self-guards (an admin cannot demote or disable themselves), so we
// mirror that here rather than offer a click that can only 4xx. Presentation
// only: no fetching, no authority re-derivation (the endpoint admin-gates).

import type { UserRow } from "../api";

export function AdminUsersPanel({
  users,
  selfEmail,
  onSetUser,
}: {
  users: UserRow[];
  selfEmail: string | null;
  onSetUser: (params: { email: string; is_admin?: boolean; enabled?: boolean }) => void;
}): JSX.Element {
  const SELF_GUARD = "You cannot change your own admin/enabled flags";
  return (
    <section className="arbor-admin-users" data-testid="admin-users-panel">
      <h2>
        Users <span className="arbor-count">{users.length}</span>
      </h2>
      {users.length === 0 ? (
        <p data-testid="admin-users-empty">No users.</p>
      ) : (
        <table className="arbor-admin-users-table" data-testid="admin-users-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Admin</th>
              <th>Enabled</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              // Mirror the backend self-guard: the viewer's own flags are inert.
              const isSelf = selfEmail != null && u.email === selfEmail;
              return (
                <tr key={u.email} data-testid={`admin-users-row-${u.email}`}>
                  <td className="arbor-admin-users-email">
                    {u.email}
                    {isSelf && <span className="arbor-admin-users-self"> (you)</span>}
                  </td>
                  <td>{u.full_name}</td>
                  <td>
                    <input
                      type="checkbox"
                      data-testid={`admin-users-admin-${u.email}`}
                      checked={u.is_admin}
                      disabled={isSelf}
                      title={isSelf ? SELF_GUARD : "Platform admin"}
                      // Belt-and-braces beside `disabled` (the server self-guards too).
                      onChange={(e) => {
                        if (!isSelf) onSetUser({ email: u.email, is_admin: e.target.checked });
                      }}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      data-testid={`admin-users-enabled-${u.email}`}
                      checked={u.enabled}
                      disabled={isSelf}
                      title={isSelf ? SELF_GUARD : "Account enabled"}
                      onChange={(e) => {
                        if (!isSelf) onSetUser({ email: u.email, enabled: e.target.checked });
                      }}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
