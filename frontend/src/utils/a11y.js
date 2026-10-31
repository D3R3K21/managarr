// Make a click-only element keyboard / TV-remote (D-pad) operable.
// Spread onto the element:  <div {...pressable(() => doThing())}>…</div>
// Adds focusability (tabIndex), button semantics, and Enter/Space activation so
// the Shield remote can select it. Pass {disabled:true} to opt a row out.
export function pressable(onClick, { disabled = false } = {}) {
  if (disabled || !onClick) return {};
  return {
    tabIndex: 0,
    role: "button",
    onClick,
    onKeyDown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onClick(e);
      }
    },
  };
}
