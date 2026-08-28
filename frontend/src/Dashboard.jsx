import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { FileAudio, Clock, Upload, FileText, Trash2, ArrowLeft } from "lucide-react";
import { supabase } from "./supabaseClient";
import NotificationModal from "./NotificationModal";
import "./Dashboard.css";

const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
    return "http://localhost:7860";
  return "";
};

const API_BASE_URL = getApiBaseUrl();

export default function Dashboard({ user }) {
  const navigate = useNavigate();

  const [stats, setStats] = useState(null);
  const [isLoadingStats, setIsLoadingStats] = useState(true);

  const [documents, setDocuments] = useState([]);
  const [isLoadingDocs, setIsLoadingDocs] = useState(true);
  const [isUploading, setIsUploading] = useState(false);

  const [notification, setNotification] = useState(null);
  const showNotification = (type, title, message) => setNotification({ type, title, message });
  const closeNotification = () => setNotification(null);

  const getAuthToken = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || "";
  };

  useEffect(() => {
    if (!user) {
      navigate("/");
      return;
    }

    const fetchStats = async () => {
      setIsLoadingStats(true);
      try {
        const token = await getAuthToken();
        const response = await fetch(`${API_BASE_URL}/api/dashboard/stats`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) throw new Error("Failed to load stats");
        const data = await response.json();
        setStats(data);
      } catch (err) {
        console.error(err);
      } finally {
        setIsLoadingStats(false);
      }
    };

    const fetchDocuments = async () => {
      setIsLoadingDocs(true);
      const { data, error } = await supabase
        .from("context_documents")
        .select("*")
        .order("created_at", { ascending: false });
      if (!error && data) setDocuments(data);
      setIsLoadingDocs(false);
    };

    fetchStats();
    fetchDocuments();
  }, [user]);

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file later
    if (!file) return;

    setIsUploading(true);
    try {
      const token = await getAuthToken();
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch(`${API_BASE_URL}/api/documents/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Upload failed");

      showNotification("success", "Document Uploaded", `${file.name} was indexed into ${data.chunks_indexed} chunk(s) for cross-referencing.`);

      const { data: docs } = await supabase
        .from("context_documents")
        .select("*")
        .order("created_at", { ascending: false });
      if (docs) setDocuments(docs);
    } catch (err) {
      showNotification("error", "Upload Failed", err.message);
    } finally {
      setIsUploading(false);
    }
  };

  const handleDelete = async (doc) => {
    try {
      const { error: storageError } = await supabase.storage
        .from("context_documents")
        .remove([doc.storage_path]);
      if (storageError) throw storageError;

      const { error: dbError } = await supabase
        .from("context_documents")
        .delete()
        .eq("id", doc.id);
      if (dbError) throw dbError;

      setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (err) {
      showNotification("error", "Delete Failed", err.message);
    }
  };

  const formatTimeProcessed = () => {
    if (!stats) return "—";
    if (stats.total_hours >= 1) return `${stats.total_hours} hrs`;
    return `${stats.total_minutes} min`;
  };

  if (!user) return null;

  return (
    <div className="dashboard-page fade-in">
      <div className="container">
        <button className="dashboard-back-btn" onClick={() => navigate("/")}>
          <ArrowLeft size={16} /> Back to Synthesis Engine
        </button>

        <h1 className="dashboard-title">Dashboard</h1>
        <p className="dashboard-subtitle">
          {user.user_metadata?.first_name ? `Welcome back, ${user.user_metadata.first_name}.` : "Welcome back."}
        </p>

        <div className="dashboard-stats-grid">
          <div className="dashboard-stat-card">
            <FileAudio size={24} className="dashboard-stat-icon" />
            <div className="dashboard-stat-value">
              {isLoadingStats ? "…" : stats?.files_processed ?? 0}
            </div>
            <div className="dashboard-stat-label">Audio Files Processed</div>
          </div>
          <div className="dashboard-stat-card">
            <Clock size={24} className="dashboard-stat-icon" />
            <div className="dashboard-stat-value">
              {isLoadingStats ? "…" : formatTimeProcessed()}
            </div>
            <div className="dashboard-stat-label">Time Processed</div>
          </div>
        </div>

        <div className="dashboard-docs-section">
          <div className="dashboard-docs-header">
            <div>
              <h2>Support Documentation</h2>
              <p className="dashboard-subtitle" style={{ marginBottom: 0 }}>
                Uploaded documents are cross-referenced by the fact-checker during livestream sessions
                and the Synthesis Engine's cross-reference feature.
              </p>
            </div>
            <label className="btn btn-primary dashboard-upload-btn">
              <Upload size={16} />
              {isUploading ? "Uploading…" : "Upload Document"}
              <input
                type="file"
                accept=".txt,.md,.csv,.pdf,.docx"
                style={{ display: "none" }}
                onChange={handleUpload}
                disabled={isUploading}
              />
            </label>
          </div>

          {isLoadingDocs ? (
            <p className="dashboard-empty-hint">Loading documents…</p>
          ) : documents.length === 0 ? (
            <div className="dashboard-docs-empty">
              <FileText size={28} />
              <p>No support documents uploaded yet.</p>
              <p className="dashboard-empty-hint">
                Upload reference material (.txt, .md, .csv, .pdf, .docx) so the fact-checker can
                cross-reference claims against it.
              </p>
            </div>
          ) : (
            <ul className="dashboard-docs-list">
              {documents.map((doc) => (
                <li key={doc.id} className="dashboard-doc-item">
                  <FileText size={16} />
                  <span className="dashboard-doc-name">{doc.file_name}</span>
                  <span className="dashboard-doc-date">
                    {new Date(doc.created_at).toLocaleDateString()}
                  </span>
                  <button
                    className="dashboard-doc-delete"
                    onClick={() => handleDelete(doc)}
                    title="Delete document"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <NotificationModal
        isOpen={!!notification}
        onClose={closeNotification}
        type={notification?.type}
        title={notification?.title}
        message={notification?.message}
      />
    </div>
  );
}
