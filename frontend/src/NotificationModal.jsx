import { useEffect, useCallback } from "react";
import {
  X,
  AlertCircle,
  AlertTriangle,
  CheckCircle,
  Info,
} from "lucide-react";
import "./NotificationModal.css";

/**
 * NotificationModal — Replaces all browser alert() calls with a polished, themed modal.
 *
 * Props:
 *   isOpen    : boolean
 *   onClose   : () => void
 *   type      : "error" | "warning" | "success" | "info"
 *   title     : string
 *   message   : string
 *   actions   : Array<{ label: string, onClick?: () => void, variant?: "primary" | "secondary" }>
 *               (defaults to a single "OK" button that closes the modal)
 */

const TYPE_CONFIG = {
  error: {
    Icon: AlertCircle,
    accentColor: "#EF4444",
    glowColor: "rgba(239, 68, 68, 0.15)",
    borderColor: "rgba(239, 68, 68, 0.3)",
    label: "Error",
  },
  warning: {
    Icon: AlertTriangle,
    accentColor: "#F59E0B",
    glowColor: "rgba(245, 158, 11, 0.15)",
    borderColor: "rgba(245, 158, 11, 0.3)",
    label: "Warning",
  },
  success: {
    Icon: CheckCircle,
    accentColor: "#10B981",
    glowColor: "rgba(16, 185, 129, 0.15)",
    borderColor: "rgba(16, 185, 129, 0.3)",
    label: "Success",
  },
  info: {
    Icon: Info,
    accentColor: "#00F0FF",
    glowColor: "rgba(0, 240, 255, 0.15)",
    borderColor: "rgba(0, 240, 255, 0.3)",
    label: "Notice",
  },
};

export default function NotificationModal({
  isOpen,
  onClose,
  type = "info",
  title,
  message,
  actions,
}) {
  const config = TYPE_CONFIG[type] || TYPE_CONFIG.info;
  const { Icon, accentColor, glowColor, borderColor } = config;

  // Close on Escape key
  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const resolvedActions = actions && actions.length > 0
    ? actions
    : [{ label: "OK", onClick: onClose, variant: "primary" }];

  return (
    <div className="notif-overlay" onClick={onClose}>
      <div
        className="notif-modal fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          borderColor: borderColor,
          boxShadow: `0 24px 60px rgba(0, 0, 0, 0.6), 0 0 30px ${glowColor}`,
        }}
      >
        {/* Close Button */}
        <button className="notif-close-btn" onClick={onClose} aria-label="Close notification">
          <X size={18} />
        </button>

        {/* Icon */}
        <div className="notif-icon-container" style={{ background: glowColor }}>
          <Icon size={28} color={accentColor} />
        </div>

        {/* Title */}
        <h3 className="notif-title" style={{ color: accentColor }}>
          {title || config.label}
        </h3>

        {/* Message */}
        <p className="notif-message">{message}</p>

        {/* Action Buttons — right-aligned */}
        <div className="notif-actions">
          {resolvedActions.map((action, idx) => (
            <button
              key={idx}
              className={`notif-btn ${action.variant === "secondary" ? "notif-btn-secondary" : "notif-btn-primary"}`}
              style={
                action.variant !== "secondary"
                  ? { background: accentColor, color: "#0A0F24" }
                  : {}
              }
              onClick={() => {
                if (action.onClick) action.onClick();
                else onClose();
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
