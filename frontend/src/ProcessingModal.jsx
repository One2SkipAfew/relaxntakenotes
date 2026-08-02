import { createPortal } from 'react-dom';
import { Loader2, X, RefreshCw, Cpu } from 'lucide-react';
import './ProcessingModal.css';

export default function ProcessingModal({ isOpen, stepMessage, onCancel, onRestart }) {
  if (!isOpen) return null;

  return createPortal(
    <div className="processing-overlay">
      <div className="processing-modal-tech fade-in">
        
        {/* Decorative Tech Elements */}
        <div className="tech-accents">
          <div className="tech-dot top-left" />
          <div className="tech-dot top-right" />
          <div className="tech-dot bottom-left" />
          <div className="tech-dot bottom-right" />
        </div>

        <div className="processing-icon-wrapper">
          <div className="processing-pulse-ring" />
          <div className="processing-pulse-ring delay" />
          <div className="processing-icon-inner">
            <Cpu className="processing-cpu-icon" size={24} />
          </div>
        </div>

        <h2 className="processing-title-tech">AI ENGINE ACTIVE</h2>
        
        <div className="processing-status-box">
          <Loader2 className="processing-spinner-tech" size={16} />
          <span className="processing-step-tech">{stepMessage || 'Processing data payload...'}</span>
        </div>

        <p className="processing-note-tech">Please remain on this secure frequency while data is processed.</p>

        <div className="processing-actions">
          {onCancel && (
            <button className="btn-tech-cancel" onClick={onCancel}>
              <X size={16} />
              <span>TERMINATE</span>
            </button>
          )}
          {onRestart && (
            <button className="btn-tech-restart" onClick={onRestart}>
              <RefreshCw size={16} />
              <span>RESTART</span>
            </button>
          )}
        </div>

      </div>
    </div>,
    document.body
  );
}
