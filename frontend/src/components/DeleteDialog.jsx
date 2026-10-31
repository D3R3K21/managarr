import React from "react";
import Modal from "./Modal";

// Delete confirmation with a keep-vs-delete-files choice.
// props: open, title, message, fileHint?, busy?, onClose, onConfirm(deleteFiles)
export default function DeleteDialog({
  open, title = "Delete", message, fileHint, busy, onClose, onConfirm,
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width={480}
      footer={
        <div className="row gap-sm" style={{ justifyContent: "flex-end" }}>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn btn-secondary btn-sm" onClick={() => onConfirm(false)} disabled={busy}>
            Keep files
          </button>
          <button className="btn btn-danger btn-sm" onClick={() => onConfirm(true)} disabled={busy}>
            Delete files
          </button>
        </div>
      }
    >
      <p style={{ margin: 0 }}>{message}</p>
      {fileHint && <p className="faint" style={{ fontSize: 13, marginTop: 10 }}>{fileHint}</p>}
    </Modal>
  );
}
