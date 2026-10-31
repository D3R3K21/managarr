import React from "react";
import { useSearchParams, Link } from "react-router-dom";
import AuthorizeDevice from "../components/AuthorizeDevice";
import { IconLogo } from "../components/icons";

// Landing page for the pairing QR code (/link?code=XXXX-XXXX). It's behind RequireAuth,
// so an un-logged-in phone is bounced to /login and returned here after signing in.
export default function LinkDevice() {
  const [params] = useSearchParams();
  const code = params.get("code") || "";

  return (
    <div className="login-wrap">
      <div className="login-card" style={{ maxWidth: 440 }}>
        <div className="login-brand" style={{ marginBottom: 18 }}>
          <IconLogo size={24} />
          <span>Authorize device</span>
        </div>
        <p className="faint" style={{ fontSize: 13, marginBottom: 16, textAlign: "center" }}>
          Confirm the code shown on your device to sign it in — no need to type your
          password on the TV.
        </p>
        <AuthorizeDevice initialCode={code} />
        <div style={{ marginTop: 18, textAlign: "center" }}>
          <Link to="/" className="faint" style={{ fontSize: 12 }}>← Back to Managarr</Link>
        </div>
      </div>
    </div>
  );
}
