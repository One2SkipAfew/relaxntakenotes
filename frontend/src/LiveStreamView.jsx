import { useState, useEffect, useRef, useCallback } from "react";
import {
  Mic,
  Square,
  Radio,
  FileText,
  Sparkles,
  Shield,
  Clock,
  ArrowLeft,
  Upload,
  CheckCircle,
  XCircle,
  AlertTriangle,
  HelpCircle,
  ChevronDown,
  ChevronUp,
  Download,
  Package,
  RefreshCw,
  Save,
} from "lucide-react";
import "./LiveStreamView.css";
import { supabase } from "./supabaseClient";
import { jsPDF } from "jspdf";

const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
    return "http://localhost:7860";
  return "";
};

const API_BASE_URL = getApiBaseUrl();

// WebSocket URL derived from API base
const getWsUrl = () => {
  const base = API_BASE_URL || window.location.origin;
  const wsProtocol = base.startsWith("https") ? "wss" : "ws";
  const host = base.replace(/^https?:\/\//, "");
  return `${wsProtocol}://${host}/ws/livestream`;
};

const formatTime = (secs) => {
  const hrs = Math.floor(secs / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  const remainingSecs = secs % 60;
  if (hrs > 0) {
    return `${hrs.toString().padStart(2, "0")}:${mins.toString().padStart(2, "0")}:${remainingSecs.toString().padStart(2, "0")}`;
  }
  return `${mins.toString().padStart(2, "0")}:${remainingSecs.toString().padStart(2, "0")}`;
};

const VERDICT_CONFIG = {
  TRUE: { icon: CheckCircle, color: "#22c55e", bg: "rgba(34, 197, 94, 0.1)", label: "TRUE" },
  FALSE: { icon: XCircle, color: "#ef4444", bg: "rgba(239, 68, 68, 0.1)", label: "FALSE" },
  MISLEADING: { icon: AlertTriangle, color: "#f59e0b", bg: "rgba(245, 158, 11, 0.1)", label: "MISLEADING" },
  UNVERIFIABLE: { icon: HelpCircle, color: "#6b7280", bg: "rgba(107, 114, 128, 0.1)", label: "UNVERIFIABLE" },
};

const MAX_DURATION_SECONDS = 5400; // 1.5 hours
const WARN_BEFORE_SECONDS = 300;   // Warn 5 minutes before limit

export default function LiveStreamView({ onBack }) {
  // --- State ---
  const [isStreaming, setIsStreaming] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [duration, setDuration] = useState(0);
  const [connectionStatus, setConnectionStatus] = useState("disconnected"); // disconnected | connecting | connected | error

  // Transcript
  const [transcriptEntries, setTranscriptEntries] = useState([]);
  const [interimText, setInterimText] = useState("");
  const [interimSpeaker, setInterimSpeaker] = useState(0);

  // AI Notes
  const [aiNotes, setAiNotes] = useState("");
  const [isGeneratingNotes, setIsGeneratingNotes] = useState(false);
  const [autoNotesInterval, setAutoNotesInterval] = useState(60); // seconds

  // Fact-Checking
  const [factCheckResults, setFactCheckResults] = useState([]);
  const [isFactChecking, setIsFactChecking] = useState(false);
  const [expandedClaim, setExpandedClaim] = useState(null);

  // Context Documents
  const [contextDocs, setContextDocs] = useState([]);
  const [useOrgDocs, setUseOrgDocs] = useState(false);

  // Meeting Package & Saving
  const [meetingPackage, setMeetingPackage] = useState("");
  const [isGeneratingPackage, setIsGeneratingPackage] = useState(false);
  const [isSavingSession, setIsSavingSession] = useState(false);
  const [sessionSaved, setSessionSaved] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);

  // Refs
  const wsRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const audioContextRef = useRef(null);
  const processorRef = useRef(null);
  const durationTimerRef = useRef(null);
  const autoNotesTimerRef = useRef(null);
  const transcriptEndRef = useRef(null);
  const transcriptContainerRef = useRef(null);
  const audioChunksRef = useRef([]);
  const mediaRecorderRef = useRef(null);

  // Auto-scroll transcript
  useEffect(() => {
    if (transcriptEndRef.current) {
      transcriptEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [transcriptEntries, interimText]);

  // Fetch existing org docs for the user
  useEffect(() => {
    const fetchDocs = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) return; // Only for logged-in users
      
      setCurrentUser(session.user);
      const { data, error } = await supabase
        .from("context_documents")
        .select("*")
        .order("created_at", { ascending: false });
        
      if (!error && data) {
        setContextDocs(data);
        if (data.length > 0) setUseOrgDocs(true);
      }
    };
    fetchDocs();
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopStreaming();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const getAuthToken = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || "";
  };

  // --- WebSocket + Audio Pipeline ---
  const startStreaming = async () => {
    try {
      setConnectionStatus("connecting");

      // 1. Get microphone access
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      mediaStreamRef.current = stream;

      // 2. Connect WebSocket
      const ws = new WebSocket(getWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectionStatus("connected");
        setIsStreaming(true);
        setDuration(0);

        // Start duration timer with auto-stop enforcement
        durationTimerRef.current = setInterval(() => {
          setDuration((prev) => {
            const next = prev + 1;
            // Warn 5 minutes before limit
            if (next === MAX_DURATION_SECONDS - WARN_BEFORE_SECONDS) {
              alert(`⚠️ Recording will automatically stop in ${WARN_BEFORE_SECONDS / 60} minutes (1.5 hour limit).`);
            }
            // Auto-stop at limit
            if (next >= MAX_DURATION_SECONDS) {
              // Use setTimeout to avoid calling stopStreaming inside setDuration
              setTimeout(() => {
                if (wsRef.current) {
                  alert("⏹️ Maximum recording duration of 1.5 hours reached. Recording has been stopped.");
                  // Trigger stop
                  document.querySelector(".ls-btn-stop")?.click();
                }
              }, 0);
            }
            return next;
          });
        }, 1000);

        // Set up MediaRecorder to capture webm audio chunks for saving
        const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
        mediaRecorderRef.current = recorder;
        audioChunksRef.current = [];
        setSessionSaved(false); // Reset saved state for new recording
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) audioChunksRef.current.push(e.data);
        };
        recorder.start(250);

        // Set up audio processing with AudioContext for PCM streaming to Deepgram
        const audioContext = new (window.AudioContext || window.webkitAudioContext)({
          sampleRate: 16000,
        });
        audioContextRef.current = audioContext;

        const source = audioContext.createMediaStreamSource(stream);

        // Use ScriptProcessorNode (deprecated but widely supported) or AudioWorklet
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        processorRef.current = processor;

        processor.onaudioprocess = (e) => {
          if (ws.readyState === WebSocket.OPEN && !isPaused) {
            const inputData = e.inputBuffer.getChannelData(0);
            // Convert float32 to int16 PCM
            const pcmData = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
              const s = Math.max(-1, Math.min(1, inputData[i]));
              pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            ws.send(pcmData.buffer);
          }
        };

        source.connect(processor);
        processor.connect(audioContext.destination);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleWebSocketMessage(msg);
        } catch (err) {
          console.error("Failed to parse WebSocket message:", err);
        }
      };

      ws.onerror = (error) => {
        console.error("WebSocket error:", error);
        setConnectionStatus("error");
      };

      ws.onclose = () => {
        setConnectionStatus("disconnected");
        if (isStreaming) {
          // Unexpected close
          setIsStreaming(false);
          cleanupAudio();
        }
      };
    } catch (err) {
      console.error("Failed to start streaming:", err);
      setConnectionStatus("error");
      alert("Could not access microphone. Please check permissions.");
    }
  };

  const handleWebSocketMessage = (msg) => {
    switch (msg.type) {
      case "transcript":
        if (msg.is_final) {
          // Add final transcript entry
          setTranscriptEntries((prev) => [
            ...prev,
            {
              id: `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              text: msg.text,
              speaker: msg.speaker || 0,
              start: msg.start || 0,
              end: msg.end || 0,
              timestamp: Date.now(),
              isFinal: true,
            },
          ]);
          setInterimText("");
          setInterimSpeaker(0);
        } else {
          // Update interim text
          setInterimText(msg.text);
          setInterimSpeaker(msg.speaker || 0);
        }
        break;

      case "status":
        console.log("LiveStream status:", msg.message);
        break;

      case "error":
        console.error("LiveStream error:", msg.message);
        break;

      default:
        break;
    }
  };

  const stopStreaming = useCallback(() => {
    // Stop auto-notes timer
    if (autoNotesTimerRef.current) {
      clearInterval(autoNotesTimerRef.current);
      autoNotesTimerRef.current = null;
    }

    // Stop duration timer
    if (durationTimerRef.current) {
      clearInterval(durationTimerRef.current);
      durationTimerRef.current = null;
    }

    // Close WebSocket
    if (wsRef.current) {
      try {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      } catch (e) {
        // ignore
      }
      wsRef.current.close();
      wsRef.current = null;
    }

    // Stop MediaRecorder
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }

    cleanupAudio();
    setIsStreaming(false);
    setIsPaused(false);
    setConnectionStatus("disconnected");
    setInterimText("");
  }, []);

  const cleanupAudio = () => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
  };

  const togglePause = () => {
    setIsPaused((prev) => !prev);
  };

  // --- Auto Notes Timer ---
  const startAutoNotesTimer = () => {
    if (autoNotesTimerRef.current) clearInterval(autoNotesTimerRef.current);
    autoNotesTimerRef.current = setInterval(() => {
      // Auto-generate notes if transcript has enough content
      generateAINotes(true);
    }, autoNotesInterval * 1000);
  };

  // --- Build full transcript text ---
  const getFullTranscript = useCallback(() => {
    return transcriptEntries
      .filter((e) => e.isFinal)
      .map((e) => `Speaker ${e.speaker}: ${e.text}`)
      .join("\n\n");
  }, [transcriptEntries]);

  // --- AI Notes ---
  const generateAINotes = async (isAuto = false) => {
    const transcript = getFullTranscript();
    if (!transcript.trim() || transcript.split(/\s+/).length < 20) {
      if (!isAuto) alert("Not enough transcript content yet. Keep recording.");
      return;
    }

    setIsGeneratingNotes(true);
    try {
      const token = await getAuthToken();
      const headers = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const response = await fetch(`${API_BASE_URL}/api/livestream/ai-notes`, {
        method: "POST",
        headers,
        body: JSON.stringify({ transcript }),
      });
      if (!response.ok) throw new Error("AI notes generation failed");
      const data = await response.json();
      setAiNotes(data.result);
    } catch (err) {
      console.error("AI notes error:", err);
      if (!isAuto) alert(`Error generating notes: ${err.message}`);
    } finally {
      setIsGeneratingNotes(false);
    }
  };

  // --- Fact-Checking ---
  const runFactCheck = async () => {
    const transcript = getFullTranscript();
    if (!transcript.trim()) {
      alert("No transcript to fact-check yet.");
      return;
    }

    setIsFactChecking(true);
    try {
      const token = await getAuthToken();
      const headers = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const response = await fetch(`${API_BASE_URL}/api/livestream/fact-check`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          transcript,
          use_org_docs: useOrgDocs,
        }),
      });
      if (!response.ok) throw new Error("Fact-check failed");
      const data = await response.json();
      setFactCheckResults((prev) => [...prev, ...(data.results || [])]);
    } catch (err) {
      console.error("Fact-check error:", err);
      alert(`Fact-check error: ${err.message}`);
    } finally {
      setIsFactChecking(false);
    }
  };

  // --- Document Upload ---
  const handleDocUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      alert("You must be logged in to upload and save organization documents.");
      return;
    }

    try {
      const filePath = `${session.user.id}/${Date.now()}_${file.name.replace(/[^a-zA-Z0-9.-]/g, '_')}`;
      
      // 1. Upload to storage
      const { error: uploadError } = await supabase.storage
        .from('context_documents')
        .upload(filePath, file);
        
      if (uploadError) throw uploadError;

      // 2. Insert into database
      const { data: dbData, error: dbError } = await supabase
        .from('context_documents')
        .insert({
          user_id: session.user.id,
          file_name: file.name,
          storage_path: filePath,
          content_type: file.type,
          size_bytes: file.size
        })
        .select()
        .single();
        
      if (dbError) throw dbError;

      setContextDocs((prev) => [dbData, ...prev]);
      setUseOrgDocs(true);
    } catch (err) {
      console.error(err);
      alert(`Upload error: ${err.message}`);
    }
  };

  // --- Meeting Package ---
  const generateMeetingPackage = async () => {
    const transcript = getFullTranscript();
    if (!transcript.trim()) {
      alert("No transcript to generate package from.");
      return;
    }

    setIsGeneratingPackage(true);
    try {
      const token = await getAuthToken();
      const headers = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const response = await fetch(`${API_BASE_URL}/api/livestream/meeting-package`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          transcript,
          ai_notes: aiNotes,
          fact_check_results: factCheckResults,
        }),
      });
      if (!response.ok) throw new Error("Package generation failed");
      const data = await response.json();
      setMeetingPackage(data.result);
    } catch (err) {
      alert(`Package error: ${err.message}`);
    } finally {
      setIsGeneratingPackage(false);
    }
  };

  // --- Save Session (Database + Audio Storage) ---
  const saveSession = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      alert("You must be logged in to save the session.");
      return;
    }

    if (audioChunksRef.current.length === 0 && transcriptEntries.length === 0) {
      alert("No data to save.");
      return;
    }

    setIsSavingSession(true);
    try {
      let audioPath = null;
      // 1. Upload audio blob if available
      if (audioChunksRef.current.length > 0) {
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const filePath = `${session.user.id}/session_${Date.now()}.webm`;
        const { error: uploadError } = await supabase.storage
          .from('session_audio')
          .upload(filePath, audioBlob);
        
        if (!uploadError) {
          audioPath = filePath;
        } else {
          console.warn("Failed to upload audio:", uploadError);
        }
      }

      // 2. Insert into livestream_sessions
      const { data: sessionData, error: sessionError } = await supabase
        .from('livestream_sessions')
        .insert({
          user_id: session.user.id,
          title: `LiveStream - ${new Date().toLocaleString()}`,
          transcript_text: getFullTranscript(),
          ai_notes: aiNotes,
          meeting_package: meetingPackage,
          audio_path: audioPath,
          duration_seconds: duration
        })
        .select()
        .single();

      if (sessionError) throw sessionError;

      // 3. Insert claims if any
      if (factCheckResults.length > 0 && sessionData) {
        const claimsData = factCheckResults.map(c => ({
          session_id: sessionData.id,
          claim_text: c.claim,
          speaker: c.speaker,
          category: c.category,
          verdict: c.verdict,
          confidence_score: c.confidence,
          explanation: c.explanation,
          key_evidence: c.key_evidence,
          used_web_search: c.used_web_search
        }));
        await supabase.from('livestream_claims').insert(claimsData);
      }

      setSessionSaved(true);
      alert("Session saved successfully!");
    } catch (err) {
      console.error(err);
      alert(`Save error: ${err.message}`);
    } finally {
      setIsSavingSession(false);
    }
  };

  // --- Export Meeting Package ---
  const downloadPackage = (format) => {
    if (!meetingPackage) return;
    
    let content = "";
    let mimeType = "";
    let extension = "";
    const dateStr = new Date().toISOString().slice(0, 10);
    const filename = `meeting_package_${dateStr}`;

    if (format === "md") {
      content = meetingPackage;
      mimeType = "text/markdown";
      extension = "md";
    } else if (format === "txt") {
      content = meetingPackage.replace(/[#*_~`>]/g, ''); // Crude markdown strip
      mimeType = "text/plain;charset=utf-8";
      extension = "txt";
    } else if (format === "doc") {
      content = `\ufeff<html xmlns:o='urn:schemas-microsoft-com:office:office' xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>
<head><title>Meeting Package</title><style>body { font-family: Arial, sans-serif; line-height: 1.6; }</style></head>
<body>
  ${renderMarkdown(meetingPackage)}
</body>
</html>`;
      mimeType = "application/msword;charset=utf-8";
      extension = "doc";
    } else if (format === "pdf") {
      try {
        const doc = new jsPDF();
        doc.setFont("helvetica", "normal");
        doc.setFontSize(16);
        doc.text("Meeting Package", 14, 20);

        doc.setFontSize(10);
        const stripMd = meetingPackage.replace(/[#*_~`>]/g, '');
        const splitText = doc.splitTextToSize(stripMd, 180);

        let y = 30;
        for (let i = 0; i < splitText.length; i++) {
          if (y > 280) {
            doc.addPage();
            y = 20;
          }
          doc.text(splitText[i], 14, y);
          y += 6;
        }
        doc.save(`${filename}.pdf`);
        return;
      } catch (err) {
        alert("Error generating PDF: " + err.message);
        return;
      }
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename}.${extension}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // --- Simple Markdown Renderer ---
  const renderMarkdown = (md) => {
    if (!md) return "";
    let html = md
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    html = html.replace(/^### (.*$)/gim, '<h3 class="ls-md-h3">$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h2 class="ls-md-h2">$1</h2>');
    html = html.replace(/^# (.*$)/gim, '<h1 class="ls-md-h1">$1</h1>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong class="ls-md-bold">$1</strong>');
    html = html.replace(/^\s*[-*+]\s+(.*$)/gim, '<li class="ls-md-li">$1</li>');
    html = html.replace(/\n/g, "<br />");
    return html;
  };

  // --- Connection status badge ---
  const StatusBadge = () => {
    const configs = {
      disconnected: { color: "#6b7280", text: "Disconnected" },
      connecting: { color: "#f59e0b", text: "Connecting..." },
      connected: { color: "#22c55e", text: "Live" },
      error: { color: "#ef4444", text: "Error" },
    };
    const cfg = configs[connectionStatus];
    return (
      <div className="ls-status-badge" style={{ "--badge-color": cfg.color }}>
        <span className="ls-status-dot" />
        <span>{cfg.text}</span>
      </div>
    );
  };

  // --- Render ---
  return (
    <div className="ls-container">
      {/* Header */}
      <header className="ls-header">
        <div className="ls-header-left">
          <button className="ls-back-btn" onClick={onBack} title="Back to main">
            <ArrowLeft size={18} />
          </button>
          <div className="ls-header-title">
            <Radio size={18} className="ls-radio-icon" />
            <h1>LiveStream</h1>
          </div>
          <StatusBadge />
        </div>

        <div className="ls-header-right">
          <div className={`ls-timer ${isStreaming && (MAX_DURATION_SECONDS - duration) <= WARN_BEFORE_SECONDS ? "ls-timer-warning" : ""}`}>
            <Clock size={14} />
            <span>{formatTime(duration)}</span>
            {isStreaming && (
              <span className="ls-timer-remaining">
                / {formatTime(MAX_DURATION_SECONDS - duration)} left
              </span>
            )}
          </div>

          {!isStreaming ? (
            <button className="ls-btn ls-btn-start" onClick={startStreaming}>
              <Mic size={16} />
              Start Recording
            </button>
          ) : (
            <div className="ls-controls">
              <button
                className={`ls-btn ls-btn-pause ${isPaused ? "paused" : ""}`}
                onClick={togglePause}
              >
                {isPaused ? "Resume" : "Pause"}
              </button>
              <button className="ls-btn ls-btn-stop" onClick={stopStreaming}>
                <Square size={14} />
                Stop
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Waveform Visualizer Bar */}
      {isStreaming && (
        <div className="ls-waveform-bar">
          {Array.from({ length: 40 }).map((_, i) => (
            <span
              key={i}
              className="ls-wave-bar"
              style={{
                animationDelay: `${i * 0.05}s`,
                opacity: isPaused ? 0.2 : 1,
              }}
            />
          ))}
        </div>
      )}

      {/* Main Three-Panel Layout */}
      <div className="ls-panels">
        {/* LEFT: Live Transcript */}
        <div className="ls-panel ls-panel-transcript">
          <div className="ls-panel-header">
            <div className="ls-panel-title">
              <FileText size={16} />
              <span>Live Transcript</span>
            </div>
            <span className="ls-panel-count">
              {transcriptEntries.length} segments
            </span>
          </div>

          <div className="ls-transcript-body" ref={transcriptContainerRef}>
            {transcriptEntries.length === 0 && !interimText ? (
              <div className="ls-transcript-empty">
                <Mic size={32} className="ls-empty-icon" />
                <p>Start recording to see your live transcript here.</p>
                <p className="ls-empty-hint">
                  Speech will be transcribed in real-time with speaker identification.
                </p>
              </div>
            ) : (
              <>
                {transcriptEntries.map((entry) => (
                  <div key={entry.id} className="ls-transcript-entry ls-entry-final">
                    <div className="ls-entry-meta">
                      <span className="ls-speaker-badge">
                        Speaker {entry.speaker}
                      </span>
                      <span className="ls-entry-time">
                        {formatTime(Math.floor(entry.start))}
                      </span>
                    </div>
                    <p className="ls-entry-text">{entry.text}</p>
                  </div>
                ))}

                {/* Interim (partial) text */}
                {interimText && (
                  <div className="ls-transcript-entry ls-entry-interim">
                    <div className="ls-entry-meta">
                      <span className="ls-speaker-badge interim">
                        Speaker {interimSpeaker}
                      </span>
                    </div>
                    <p className="ls-entry-text interim">{interimText}</p>
                  </div>
                )}
                <div ref={transcriptEndRef} />
              </>
            )}
          </div>
        </div>

        {/* CENTER: AI Notes & Action Items */}
        <div className="ls-panel ls-panel-notes">
          <div className="ls-panel-header">
            <div className="ls-panel-title">
              <Sparkles size={16} />
              <span>AI Notes</span>
            </div>
            <button
              className="ls-btn ls-btn-sm"
              onClick={() => generateAINotes(false)}
              disabled={isGeneratingNotes || transcriptEntries.length === 0}
            >
              <RefreshCw size={12} className={isGeneratingNotes ? "spinning" : ""} />
              {isGeneratingNotes ? "Generating..." : "Generate"}
            </button>
          </div>

          <div className="ls-notes-body">
            {aiNotes ? (
              <div
                className="ls-notes-content"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(aiNotes) }}
              />
            ) : (
              <div className="ls-notes-empty">
                <Sparkles size={28} className="ls-empty-icon" />
                <p>AI-generated notes will appear here.</p>
                <p className="ls-empty-hint">
                  Notes auto-generate every {autoNotesInterval}s during recording, or click
                  Generate to create them manually.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* RIGHT: Fact-Check Results */}
        <div className="ls-panel ls-panel-factcheck">
          <div className="ls-panel-header">
            <div className="ls-panel-title">
              <Shield size={16} />
              <span>Fact Check</span>
            </div>
            <button
              className="ls-btn ls-btn-sm ls-btn-factcheck"
              onClick={runFactCheck}
              disabled={isFactChecking || transcriptEntries.length === 0}
            >
              <Shield size={12} className={isFactChecking ? "spinning" : ""} />
              {isFactChecking ? "Checking..." : "Check Claims"}
            </button>
          </div>

          {/* Document Upload Section - Authenticated Users Only */}
          {currentUser && (
            <div className="ls-docs-section">
              <label className="ls-doc-upload-btn">
                <Upload size={12} />
                <span>Upload Org Docs</span>
                <input
                  type="file"
                  accept=".txt,.md,.csv,.pdf,.doc,.docx"
                  style={{ display: "none" }}
                  onChange={handleDocUpload}
                />
              </label>
              {contextDocs.length > 0 && (
                <span className="ls-docs-count">
                  {contextDocs.length} doc{contextDocs.length > 1 ? "s" : ""} loaded
                </span>
              )}
            </div>
          )}

          <div className="ls-factcheck-body">
            {factCheckResults.length === 0 ? (
              <div className="ls-factcheck-empty">
                <Shield size={28} className="ls-empty-icon" />
                <p>Fact-check results will appear here.</p>
                <p className="ls-empty-hint">
                  Claims are detected from the transcript and verified against web sources.
                </p>
              </div>
            ) : (
              <div className="ls-claims-list">
                {factCheckResults.map((result, idx) => {
                  const verdictCfg = VERDICT_CONFIG[result.verdict] || VERDICT_CONFIG.UNVERIFIABLE;
                  const VerdictIcon = verdictCfg.icon;
                  const isExpanded = expandedClaim === idx;

                  return (
                    <div
                      key={idx}
                      className="ls-claim-card"
                      style={{ "--verdict-color": verdictCfg.color, "--verdict-bg": verdictCfg.bg }}
                      onClick={() => setExpandedClaim(isExpanded ? null : idx)}
                    >
                      <div className="ls-claim-header">
                        <div className="ls-verdict-badge">
                          <VerdictIcon size={14} />
                          <span>{verdictCfg.label}</span>
                        </div>
                        <div className="ls-confidence-bar">
                          <div
                            className="ls-confidence-fill"
                            style={{ width: `${(result.confidence || 0) * 100}%` }}
                          />
                          <span>{Math.round((result.confidence || 0) * 100)}%</span>
                        </div>
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </div>

                      <p className="ls-claim-text">{result.claim}</p>

                      {result.speaker && (
                        <span className="ls-claim-speaker">{result.speaker}</span>
                      )}

                      {isExpanded && (
                        <div className="ls-claim-details">
                          {result.explanation && (
                            <div className="ls-detail-section">
                              <h4>Explanation</h4>
                              <p>{result.explanation}</p>
                            </div>
                          )}
                          {result.key_evidence && (
                            <div className="ls-detail-section">
                              <h4>Key Evidence</h4>
                              <p>{result.key_evidence}</p>
                            </div>
                          )}
                          {result.sources && result.sources.length > 0 && (
                            <div className="ls-detail-section">
                              <h4>Sources</h4>
                              <ul className="ls-sources-list">
                                {result.sources.map((src, si) => (
                                  <li key={si}>
                                    <a href={src.url} target="_blank" rel="noopener noreferrer">
                                      {src.title}
                                    </a>
                                    {src.snippet && <p>{src.snippet}</p>}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                          <div className="ls-search-badge">
                            {result.used_web_search ? "✓ Web search verified" : "⚠ LLM-only evaluation"}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Bottom Bar */}
      <div className="ls-bottom-bar">
        <button
          className="ls-btn ls-btn-package"
          onClick={generateMeetingPackage}
          disabled={isGeneratingPackage || transcriptEntries.length === 0}
        >
          <Package size={16} />
          {isGeneratingPackage ? "Generating Package..." : "Generate Meeting Package"}
        </button>

        {/* Export Buttons */}
        {meetingPackage && (
          <div className="ls-export-group">
            <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginRight: "8px" }}>Export:</span>
            <button className="ls-btn ls-btn-sm" onClick={() => downloadPackage("md")} title="Markdown">
              .md
            </button>
            <button className="ls-btn ls-btn-sm" onClick={() => downloadPackage("txt")} title="Text File">
              .txt
            </button>
            <button className="ls-btn ls-btn-sm" onClick={() => downloadPackage("doc")} title="Word Document">
              .doc
            </button>
            <button className="ls-btn ls-btn-sm" onClick={() => downloadPackage("pdf")} title="PDF">
              .pdf
            </button>
          </div>
        )}

        {/* Save Session */}
        {!isStreaming && transcriptEntries.length > 0 && (
          <button
            className="ls-btn ls-btn-save"
            onClick={saveSession}
            disabled={isSavingSession || sessionSaved}
          >
            <Save size={16} />
            {sessionSaved ? "Saved" : (isSavingSession ? "Saving..." : "Save Session")}
          </button>
        )}
      </div>

      {/* Meeting Package Modal */}
      {meetingPackage && (
        <div className="ls-package-overlay" onClick={() => setMeetingPackage("")}>
          <div className="ls-package-modal" onClick={(e) => e.stopPropagation()}>
            <div className="ls-package-header">
              <h2>Meeting Package</h2>
              <button className="ls-btn ls-btn-sm" onClick={() => setMeetingPackage("")}>
                Close
              </button>
            </div>
            <div
              className="ls-package-content"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(meetingPackage) }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
