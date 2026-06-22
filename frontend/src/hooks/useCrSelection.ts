// Selection model for bulk Change Request approve/reject (SPEC P2). The set can
// ONLY ever contain CRs the viewer is allowed to decide (viewer_is_approver):
// every mutator filters against the actionable subset, so the invariant holds
// regardless of caller. Mirrors the snapshot-driven philosophy — the hook
// re-derives no ACL; it trusts the server-computed `viewer_is_approver` flag
// already on each ChangeRequestView.

import { useCallback, useMemo, useState } from "react";
import type { ChangeRequestView } from "../api";

export type CrSelection = {
  // selected CR ids (always a subset of the actionable ids)
  selected: Set<string>;
  // ids the viewer may act on (viewer_is_approver === true)
  actionableIds: string[];
  selectedCount: number;
  // are all actionable CRs currently selected? (false when none actionable)
  allActionableSelected: boolean;
  isSelected: (name: string) => boolean;
  // toggle one row — no-op if the CR is not in the actionable subset
  toggle: (name: string) => void;
  // select-all toggle over ONLY the actionable subset
  toggleAll: () => void;
  clear: () => void;
};

export function useCrSelection(crs: ChangeRequestView[]): CrSelection {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const actionableIds = useMemo(
    () => crs.filter((cr) => cr.viewer_is_approver).map((cr) => cr.name),
    [crs],
  );
  const actionableSet = useMemo(() => new Set(actionableIds), [actionableIds]);

  // Prune any selected id that is no longer actionable/present (e.g. the CR left
  // the queue after a refetch). Derived so we never hold a stale selection.
  const effectiveSelected = useMemo(() => {
    const next = new Set<string>();
    for (const name of selected) if (actionableSet.has(name)) next.add(name);
    return next;
  }, [selected, actionableSet]);

  const isSelected = useCallback(
    (name: string) => effectiveSelected.has(name),
    [effectiveSelected],
  );

  const toggle = useCallback(
    (name: string) => {
      if (!actionableSet.has(name)) return;
      setSelected((prev) => {
        const next = new Set<string>();
        // rebuild from the actionable-filtered view to drop any stale ids
        for (const n of prev) if (actionableSet.has(n)) next.add(n);
        if (next.has(name)) next.delete(name);
        else next.add(name);
        return next;
      });
    },
    [actionableSet],
  );

  const allActionableSelected =
    actionableIds.length > 0 && actionableIds.every((n) => effectiveSelected.has(n));

  const toggleAll = useCallback(() => {
    setSelected(() => {
      if (allActionableSelected) return new Set();
      return new Set(actionableIds);
    });
  }, [allActionableSelected, actionableIds]);

  const clear = useCallback(() => setSelected(new Set()), []);

  return {
    selected: effectiveSelected,
    actionableIds,
    selectedCount: effectiveSelected.size,
    allActionableSelected,
    isSelected,
    toggle,
    toggleAll,
    clear,
  };
}
