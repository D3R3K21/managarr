import React from "react";

export default function PageHeader({ title, subtitle, actions, children }) {
  return (
    <div className="page-header">
      <div>
        <div className="page-title">{title}</div>
        {subtitle && <div className="page-subtitle">{subtitle}</div>}
      </div>
      {children}
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}
