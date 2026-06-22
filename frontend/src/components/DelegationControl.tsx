// Branch delegation control — the structural-ownership governance surface
// (delegateBranch / revokeDelegation). Built but previously unwired: the
// capabilities were backend + agent only with no Web-UI affordance. Like every
// other surface this is a thin shell over executeAction — it re-derives no ACL
// (the delegate options come from the snapshot's can_change_structure hints and
// each grant's can_revoke flag) and performs no raw write.

import { useState } from "react";
import type { BranchGrantView, SnapshotNode } from "../api";

export function DelegationControl({
  sheet,
  grants,
  delegatableNodes,
  nodeLabel,
  onDelegate,
  onRevoke,
}: {
  sheet: string;
  // active branch delegations on this sheet (snapshot.viewer.branch_grants)
  grants: BranchGrantView[];
  // nodes the viewer may delegate (can_change_structure === true)
  delegatableNodes: SnapshotNode[];
  // resolve a node id to its display label for the grant list / picker
  nodeLabel: (node: string) => string;
  onDelegate: (params: Record<string, unknown>) => void;
  onRevoke: (params: Record<string, unknown>) => void;
}): JSX.Element {
  const [branchRoot, setBranchRoot] = useState("");
  const [grantee, setGrantee] = useState("");

  const canDelegate = branchRoot !== "" && grantee.trim() !== "";

  return (
    <section className="arbor-delegation" data-testid="delegation-control">
      <h2>
        Branch delegations <span className="arbor-count">{grants.length}</span>
      </h2>

      {grants.length > 0 && (
        <ul className="arbor-grants" data-testid="delegation-grants">
          {grants.map((g) => (
            <li
              key={g.name}
              className="arbor-grant"
              data-testid={`grant-${g.name}`}
              data-grantee={g.grantee}
            >
              <span className="arbor-grant-subject">
                <span className="arbor-grant-branch">{nodeLabel(g.branch_root)}</span>
                <span className="arbor-grant-arrow"> → </span>
                <span className="arbor-grant-grantee">{g.grantee}</span>
              </span>
              {g.can_revoke && (
                <button
                  type="button"
                  data-testid={`revoke-${g.name}`}
                  onClick={() => onRevoke({ branch_grant: g.name })}
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {delegatableNodes.length > 0 && (
        <div className="arbor-delegate-form" data-testid="delegate-form">
          <label className="arbor-field">
            <span className="arbor-field-label">Branch</span>
            <select
              data-testid="delegate-branch"
              value={branchRoot}
              onChange={(e) => setBranchRoot(e.target.value)}
            >
              <option value="">Select a branch…</option>
              {delegatableNodes.map((n) => (
                <option key={n.name} value={n.name}>
                  {nodeLabel(n.name)}
                </option>
              ))}
            </select>
          </label>
          <label className="arbor-field">
            <span className="arbor-field-label">Grantee</span>
            <input
              data-testid="delegate-grantee"
              placeholder="grantee (user)"
              value={grantee}
              onChange={(e) => setGrantee(e.target.value)}
            />
          </label>
          <button
            type="button"
            data-testid="delegate-submit"
            disabled={!canDelegate}
            onClick={() => {
              if (!canDelegate) return;
              onDelegate({ sheet, branch_root: branchRoot, grantee: grantee.trim() });
              setBranchRoot("");
              setGrantee("");
            }}
          >
            Delegate
          </button>
        </div>
      )}
    </section>
  );
}
