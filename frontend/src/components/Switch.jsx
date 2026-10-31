import React from "react";

export default function Switch({ checked, onChange, disabled, title }) {
  return (
    <label className="switch" title={title} onClick={(e) => e.stopPropagation()}>
      <input
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange && onChange(e.target.checked)}
      />
      <span className="slider" />
    </label>
  );
}
