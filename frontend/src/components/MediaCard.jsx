import React from "react";
import { Link } from "react-router-dom";

// Poster card. props: to, poster, title, sub, corner?
export default function MediaCard({ to, poster, title, sub, corner, fallback = "🎬" }) {
  const inner = (
    <div className="media-card">
      {corner && <div className="card-corner">{corner}</div>}
      {poster ? (
        <img className="media-card-poster" src={poster} alt={title} loading="lazy" />
      ) : (
        <div className="media-card-poster placeholder">{fallback}</div>
      )}
      <div className="media-card-body">
        <div className="media-card-title" title={title}>{title}</div>
        {sub && <div className="media-card-sub">{sub}</div>}
      </div>
    </div>
  );
  return to ? <Link to={to}>{inner}</Link> : inner;
}
