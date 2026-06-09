import React, { useState, useEffect, useRef } from "react";
import { 
  Mic, 
  Square, 
  Upload, 
  FileAudio, 
  Music, 
  Users, 
  FileText, 
  Sparkles, 
  ArrowRight, 
  Download, 
  Volume2, 
  RotateCcw, 
  AlertTriangle,
  Play,
  Pause,
  Clock,
  Languages,
  CheckCircle,
  TrendingUp
} from "lucide-react";
import { jsPDF } from "jspdf";

// API configuration helper
const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  // Fallback depending on where we are running
  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
    return "http://localhost:7860";
  }
  // Relational URL for production deployed on same space
  return "";
};

const API_BASE_URL = getApiBaseUrl();

// Generate or fetch user UUID
const getOrCreateUserUuid = () => {
  let uuid = localStorage.getItem("x-user-uuid");
  if (!uuid) {
    uuid = "user_" + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
    localStorage.setItem("x-user-uuid", uuid);
  }
  return uuid;
};

export default function App() {
  const userUuid = getOrCreateUserUuid();

  // App states
  const [status, setStatus] = useState({
    global_usage_minutes: 0,
    global_limit_minutes: 3500,
    user_usage_minutes: 0,
    user_limit_minutes: 60,
    is_over_budget: false,
    user_is_over_limit: false,
    max_recording_duration_minutes: 30
  });

  const [recordingType, setRecordingType] = useState("Meeting/Hearing");
  
  // Metadata state
  const [metadata, setMetadata] = useState({
    songArtist: "",
    songTitle: "",
    songLocation: "",
    songDate: "",
    meetingTitle: "",
    meetingDate: new Date().toISOString().split("T")[0],
    meetingTime: "",
    meetingLocation: "",
    meetingParticipants: "",
    meetingPurpose: "",
    meetingAgenda: "",
    memoTitle: "",
    memoDate: new Date().toISOString().split("T")[0]
  });

  // Audio & file selection states
  const [audioFile, setAudioFile] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [dragOver, setDragOver] = useState(false);

  // Transcription & Staging states
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingStep, setProcessingStep] = useState("");
  const [transcript, setTranscript] = useState("");
  const [documentTitle, setDocumentTitle] = useState("");
  
  // AI states
  const [aiActiveTab, setAiActiveTab] = useState("summary");
  const [aiSummary, setAiSummary] = useState("");
  const [aiInsights, setAiInsights] = useState("");
  const [aiTranslation, setAiTranslation] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("French");
  const [isAiLoading, setIsAiLoading] = useState(false);

  // TTS states
  const [ttsVoice, setTtsVoice] = useState("en-US-JennyNeural");
  const [isTtsSynthesizing, setIsTtsSynthesizing] = useState(false);
  const [ttsAudioUrl, setTtsAudioUrl] = useState(null);

  // References
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const recordingTimerRef = useRef(null);
  const audioPlayerRef = useRef(null);

  // Fetch status on mount
  useEffect(() => {
    fetchStatus();
  }, []);

  const fetchStatus = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/status`, {
        headers: {
          "X-User-UUID": userUuid
        }
      });
      if (response.ok) {
        const data = await response.json();
        setStatus(data);
      }
    } catch (err) {
      console.error("Error fetching usage status:", err);
    }
  };

  // Recording handler
  const startRecording = async () => {
    audioChunksRef.current = [];
    setAudioFile(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorderRef.current = new MediaRecorder(stream);
      
      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorderRef.current.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        // Create file object
        const file = new File([audioBlob], "recorded_audio.webm", { type: "audio/webm" });
        setAudioFile(file);
        // Stop all tracks to release mic
        stream.getTracks().forEach(track => track.stop());
      };

      mediaRecorderRef.current.start(250);
      setIsRecording(true);
      setRecordingDuration(0);
      
      recordingTimerRef.current = setInterval(() => {
        setRecordingDuration(prev => {
          // Safety cap at max recording minutes
          if (prev >= status.max_recording_duration_minutes * 60) {
            stopRecording();
            return prev;
          }
          return prev + 1;
        });
      }, 1000);
      
    } catch (err) {
      alert("Could not access microphone. Please check permissions.");
      console.error(err);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
      }
    }
  };

  // File drag & drop
  const handleDragOver = (e) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      if (file.type.startsWith("audio/")) {
        setAudioFile(file);
      } else {
        alert("Please select an audio file (mp3, wav, m4a, webm, etc.)");
      }
    }
  };

  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      setAudioFile(e.target.files[0]);
    }
  };

  // Submit file for transcription
  const handleTranscribe = async () => {
    if (!audioFile) return;

    setIsProcessing(true);
    setProcessingStep("Uploading audio payload and initiating transcription...");
    setTranscript("");

    // Package metadata context
    let selectedMeta = {};
    let finalTitle = "";
    if (recordingType === "Song") {
      selectedMeta = {
        type: "Song",
        artist: metadata.songArtist,
        title: metadata.songTitle,
        location: metadata.songLocation,
        date: metadata.songDate
      };
      finalTitle = metadata.songTitle || metadata.songArtist ? `${metadata.songArtist} - ${metadata.songTitle}` : "Untitled Song";
    } else if (recordingType === "Meeting/Hearing") {
      selectedMeta = {
        type: "Meeting/Hearing",
        title: metadata.meetingTitle,
        date: metadata.meetingDate,
        time: metadata.meetingTime,
        location: metadata.meetingLocation,
        participants: metadata.meetingParticipants,
        purpose: metadata.meetingPurpose,
        agenda: metadata.meetingAgenda
      };
      finalTitle = metadata.meetingTitle || `Meeting ${metadata.meetingDate}`;
    } else {
      selectedMeta = {
        type: "Memo/Voice Note",
        title: metadata.memoTitle,
        date: metadata.memoDate
      };
      finalTitle = metadata.memoTitle || `Memo ${metadata.memoDate}`;
    }
    setDocumentTitle(finalTitle);

    const formData = new FormData();
    formData.append("file", audioFile);

    try {
      setProcessingStep("Transcribing via Deepgram. Identifying speakers...");
      const response = await fetch(`${API_BASE_URL}/api/transcribe`, {
        method: "POST",
        headers: {
          "X-User-UUID": userUuid
        },
        body: formData
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Transcription failed");
      }

      const data = await response.json();
      
      // Build speaker-diarized text format
      let transcriptText = "";
      if (data.paragraphs && data.paragraphs.length > 0) {
        transcriptText = data.paragraphs.map(p => `${p.speaker}: ${p.text}`).join("\n\n");
      } else {
        transcriptText = data.raw_transcript;
      }
      
      setTranscript(transcriptText);
      setIsProcessing(false);
      
      // Update limits stats
      fetchStatus();
      
    } catch (err) {
      alert(`Error transcribing audio: ${err.message}`);
      setIsProcessing(false);
    }
  };

  // Generate AI Features
  const triggerAiFeature = async (type) => {
    if (!transcript) return;
    setIsAiLoading(true);

    let selectedMeta = {};
    if (recordingType === "Song") {
      selectedMeta = {
        type: "Song",
        artist: metadata.songArtist,
        title: metadata.songTitle,
        location: metadata.songLocation
      };
    } else if (recordingType === "Meeting/Hearing") {
      selectedMeta = {
        type: "Meeting/Hearing",
        title: metadata.meetingTitle,
        participants: metadata.meetingParticipants,
        agenda: metadata.meetingAgenda
      };
    } else {
      selectedMeta = {
        type: "Memo/Voice Note",
        title: metadata.memoTitle
      };
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/ai-features`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          transcript: transcript,
          feature_type: type,
          metadata: selectedMeta,
          target_language: targetLanguage
        })
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "AI execution failed");
      }

      const data = await response.json();
      if (type === "summary") {
        setAiSummary(data.result);
      } else if (type === "insights") {
        setAiInsights(data.result);
      } else if (type === "translation") {
        setAiTranslation(data.result);
      }
    } catch (err) {
      alert(`AI Error: ${err.message}`);
    } finally {
      setIsAiLoading(false);
    }
  };

  // Text-To-Speech Synthesis
  const handleTtsSynthesis = async () => {
    if (!transcript) return;
    
    setIsTtsSynthesizing(true);
    if (ttsAudioUrl) {
      URL.revokeObjectURL(ttsAudioUrl);
      setTtsAudioUrl(null);
    }

    // Clean transcript text for TTS (remove speaker labels to sound natural)
    const cleanText = transcript.replace(/Speaker \d+:/g, "");

    try {
      const response = await fetch(`${API_BASE_URL}/api/tts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          text: cleanText.substring(0, 3000), // Limit character length for performance
          voice: ttsVoice
        })
      });

      if (!response.ok) {
        throw new Error("TTS generation failed");
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      setTtsAudioUrl(url);
    } catch (err) {
      alert(`TTS error: ${err.message}`);
    } finally {
      setIsTtsSynthesizing(false);
    }
  };

  // Format recording timer
  const formatTime = (secs) => {
    const mins = Math.floor(secs / 60);
    const remainingSecs = secs % 60;
    return `${mins.toString().padStart(2, "0")}:${remainingSecs.toString().padStart(2, "0")}`;
  };

  // Simple custom Markdown parser to avoid large bundles
  const renderMarkdown = (md) => {
    if (!md) return "";
    let html = md;
    
    // Header level 3
    html = html.replace(/^### (.*$)/gim, '<h3 style="font-family: var(--font-heading); color: var(--accent-teal); font-size: 1.1rem; margin: 16px 0 8px 0;">$1</h3>');
    // Header level 2
    html = html.replace(/^## (.*$)/gim, '<h2 style="font-family: var(--font-heading); color: var(--text-primary); font-size: 1.3rem; margin: 20px 0 10px 0;">$1</h2>');
    // Header level 1
    html = html.replace(/^# (.*$)/gim, '<h1 style="font-family: var(--font-heading); color: var(--text-primary); font-size: 1.5rem; margin: 24px 0 12px 0; border-bottom: 1px solid var(--border-color); padding-bottom: 4px;">$1</h1>');
    
    // Bold
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--text-primary); font-weight:600;">$1</strong>');
    
    // Bullet lists
    html = html.replace(/^\s*[-*+]\s+(.*$)/gim, '<li style="margin-left: 20px; margin-bottom: 6px; list-style-type: square; color: var(--text-secondary);">$1</li>');
    
    // Wrap paragraph line breaks
    html = html.replace(/\n/g, '<br />');
    
    return html;
  };

  // Download utilities
  const downloadTXT = () => {
    const blob = new Blob([transcript], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${documentTitle.toLowerCase().replace(/\s+/g, "_")}_transcript.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const downloadWord = () => {
    const htmlContent = `
      <html xmlns:o='urn:schemas-microsoft-com:office:office' xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>
      <head><title>${documentTitle}</title><style>body { font-family: Arial, sans-serif; line-height: 1.6; }</style></head>
      <body>
        <h2>${documentTitle}</h2>
        <p style="white-space: pre-wrap;">${transcript.replace(/\n/g, '<br/>')}</p>
      </body>
      </html>
    `;
    const blob = new Blob(['\ufeff' + htmlContent], { type: 'application/msword' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${documentTitle.toLowerCase().replace(/\s+/g, "_")}_transcript.doc`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const downloadPDF = () => {
    const doc = new jsPDF();
    doc.setFont("helvetica", "normal");
    
    doc.setFontSize(16);
    doc.text(documentTitle || "relaxntakenotes.africa Transcript", 14, 20);
    
    doc.setFontSize(10);
    const splitText = doc.splitTextToSize(transcript, 180);
    
    let y = 30;
    for (let i = 0; i < splitText.length; i++) {
      if (y > 280) {
        doc.addPage();
        y = 20;
      }
      doc.text(splitText[i], 14, y);
      y += 6;
    }
    doc.save(`${documentTitle.toLowerCase().replace(/\s+/g, "_")}_transcript.pdf`);
  };

  // If global budget is exceeded, render "Return Shortly" page immediately
  if (status.is_over_budget) {
    return (
      <div class="container fade-in">
        <div class="over-budget-container">
          <div class="over-budget-icon">🌙</div>
          <h1 style={{ fontSize: "2.5rem", marginBottom: "16px", color: "var(--accent-gold)" }}>High Traffic Volatility</h1>
          <h2 style={{ fontSize: "1.5rem", marginBottom: "24px", color: "var(--text-primary)" }}>We will return shortly!</h2>
          <p style={{ marginBottom: "32px", fontSize: "1.1rem" }}>
            The free server budget for transcriptions has been reached for this month. 
            We cap resource consumption to ensure free plans remain viable for everyone. 
            Service will resume automatically next month.
          </p>
          <div style={{ display: "flex", gap: "16px" }}>
            <button className="btn btn-secondary" onClick={fetchStatus}>Check Again</button>
            <a href="mailto:support@relaxntakenotes.africa" className="btn btn-primary">Request Pro Extension</a>
          </div>
          <div style={{ marginTop: "40px" }} className="text-muted-small">
            Monthly budget cycles reset on the 1st of each month.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="container fade-in" style={{ paddingBottom: "80px" }}>
      {/* Header */}
      <header className="header">
        <div className="logo" onClick={() => { setTranscript(""); setAudioFile(null); }}>
          🎙️ relaxntakenotes<span className="logo-dot">.africa</span>
        </div>
        
        <div className="flex-center">
          <div className="budget-badge">
            <Clock size={14} />
            <span>Usage: {status.user_usage_minutes} / {status.user_limit_minutes} min (User)</span>
          </div>
          <div className={`budget-badge ${status.global_usage_minutes >= status.global_limit_minutes * 0.9 ? 'budget-alert' : ''}`}>
            <TrendingUp size={14} />
            <span>Global Server: {status.global_usage_minutes} / {status.global_limit_minutes} min</span>
          </div>
        </div>
      </header>

      {/* Main Panel - Switch between Input / Staging Area */}
      {!transcript ? (
        <div style={{ maxWidth: "800px", margin: "0 auto" }}>
          {/* Hero Headline */}
          <div style={{ textAlign: "center", marginBottom: "48px" }}>
            <h1 style={{ fontSize: "3rem", lineHeight: "1.2", marginBottom: "16px", background: "linear-gradient(135deg, #FFF 40%, var(--accent-purple) 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              Relax, I'll take Notes.
            </h1>
            <p style={{ fontSize: "1.2rem", maxWidth: "600px", margin: "0 auto" }}>
              Let artificial intelligence listen, parse, and write. Record or upload audio files up to {status.max_recording_duration_minutes} minutes, fill in custom metadata context, and staging writes clean minutes, action points, translations, or synthetic voice playback.
            </p>
          </div>

          <div className="card">
            {/* Limitations Notice banner */}
            <div style={{ display: "flex", gap: "12px", background: "rgba(255, 183, 3, 0.08)", border: "1px solid rgba(255, 183, 3, 0.2)", padding: "16px", borderRadius: "var(--radius-md)", marginBottom: "32px" }}>
              <AlertTriangle className="text-warning" style={{ flexShrink: 0 }} />
              <div>
                <strong style={{ color: "var(--accent-gold)" }}>Free Account Limits:</strong>
                <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)" }}>
                  Each recording is limited to {status.max_recording_duration_minutes} minutes. 
                  Personal monthly cap is {status.user_limit_minutes} minutes. 
                  Please restrict uploads to avoid hitting your limit early.
                </p>
              </div>
            </div>

            {/* Step 1: Recording Type selector */}
            <div className="form-group">
              <label>Recording Category</label>
              <div style={{ display: "flex", gap: "8px", overflowX: "auto", paddingBottom: "4px" }}>
                <button 
                  className={`btn ${recordingType === "Meeting/Hearing" ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setRecordingType("Meeting/Hearing")}
                >
                  <Users size={16} /> Meeting or Hearing
                </button>
                <button 
                  className={`btn ${recordingType === "Song" ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setRecordingType("Song")}
                >
                  <Music size={16} /> Song / Music Lyrics
                </button>
                <button 
                  className={`btn ${recordingType === "Memo/Voice Note" ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setRecordingType("Memo/Voice Note")}
                >
                  <FileText size={16} /> Memo or Voice Note
                </button>
              </div>
            </div>

            {/* Step 2: Widget File Upload / Mic Recording */}
            <div className="form-group" style={{ marginTop: "24px" }}>
              <label>Audio Source</label>
              
              {!audioFile && !isRecording ? (
                <div 
                  className={`recording-widget ${dragOver ? 'drag-over' : ''}`}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  onClick={() => document.getElementById("file-input").click()}
                >
                  <button className="mic-button" onClick={(e) => { e.stopPropagation(); startRecording(); }}>
                    <Mic size={32} />
                  </button>
                  <h3 style={{ marginBottom: "8px" }}>Click to Record or Drag Audio File Here</h3>
                  <p className="text-muted-small">Supports mp3, wav, m4a, webm, ogg. Max {status.max_recording_duration_minutes} minutes.</p>
                  <input 
                    id="file-input" 
                    type="file" 
                    accept="audio/*" 
                    style={{ display: "none" }} 
                    onChange={handleFileSelect} 
                  />
                </div>
              ) : isRecording ? (
                <div className="recording-widget" style={{ borderColor: "var(--error)", background: "rgba(239, 68, 68, 0.02)" }}>
                  <button className="mic-button recording" onClick={stopRecording}>
                    <Square size={28} />
                  </button>
                  <h3 style={{ color: "var(--error)", marginBottom: "4px" }}>Recording Active</h3>
                  <div className="wave-container">
                    <span className="wave-bar"></span>
                    <span className="wave-bar"></span>
                    <span className="wave-bar"></span>
                    <span className="wave-bar"></span>
                    <span className="wave-bar"></span>
                    <span className="wave-bar"></span>
                    <span className="wave-bar"></span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", justifyContent: "center", fontFamily: "var(--font-heading)", fontSize: "1.5rem", fontWeight: "700" }}>
                    <Clock size={20} className="text-error" />
                    <span>{formatTime(recordingDuration)}</span>
                  </div>
                  <button 
                    className="btn btn-secondary margin-top-md" 
                    onClick={() => { stopRecording(); setAudioFile(null); }}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="recording-widget" style={{ borderColor: "var(--accent-teal)" }}>
                  <div style={{ display: "flex", gap: "16px", alignItems: "center", marginBottom: "16px" }}>
                    <FileAudio size={48} className="text-teal" />
                    <div style={{ textAlign: "left" }}>
                      <h4 style={{ wordBreak: "break-all" }}>{audioFile.name}</h4>
                      <p className="text-muted-small">Size: {(audioFile.size / 1024 / 1024).toFixed(2)} MB</p>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "12px" }}>
                    <button className="btn btn-secondary" onClick={() => setAudioFile(null)}>
                      <RotateCcw size={16} /> Reset
                    </button>
                    <label className="btn btn-secondary" htmlFor="file-input-change" style={{ cursor: "pointer" }}>
                      <Upload size={16} /> Change File
                    </label>
                    <input 
                      id="file-input-change" 
                      type="file" 
                      accept="audio/*" 
                      style={{ display: "none" }} 
                      onChange={handleFileSelect} 
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Step 3: Dynamic Metadata Fields */}
            <div className="margin-top-md" style={{ borderTop: "1px solid var(--border-color)", paddingTop: "24px" }}>
              <h3 style={{ marginBottom: "16px", display: "flex", alignItems: "center", gap: "8px" }}>
                <span>Metadata Context</span>
                <span className="text-muted-small">(Optional, improves AI note quality)</span>
              </h3>

              {recordingType === "Song" && (
                <div className="metadata-grid">
                  <div className="form-group">
                    <label>Artist Name</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Miriam Makeba" 
                      className="form-input" 
                      value={metadata.songArtist}
                      onChange={(e) => setMetadata({...metadata, songArtist: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Song Title</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Qongqothwane" 
                      className="form-input"
                      value={metadata.songTitle}
                      onChange={(e) => setMetadata({...metadata, songTitle: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Recording Location</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Johannesburg Studio" 
                      className="form-input"
                      value={metadata.songLocation}
                      onChange={(e) => setMetadata({...metadata, songLocation: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Release Date</label>
                    <input 
                      type="date" 
                      className="form-input"
                      value={metadata.songDate}
                      onChange={(e) => setMetadata({...metadata, songDate: e.target.value})}
                    />
                  </div>
                </div>
              )}

              {recordingType === "Meeting/Hearing" && (
                <div className="metadata-grid">
                  <div className="form-group full-width">
                    <label>Meeting Title</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Quarterly Development Strategy" 
                      className="form-input" 
                      value={metadata.meetingTitle}
                      onChange={(e) => setMetadata({...metadata, meetingTitle: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Meeting Date</label>
                    <input 
                      type="date" 
                      className="form-input"
                      value={metadata.meetingDate}
                      onChange={(e) => setMetadata({...metadata, meetingDate: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Time</label>
                    <input 
                      type="time" 
                      className="form-input"
                      value={metadata.meetingTime}
                      onChange={(e) => setMetadata({...metadata, meetingTime: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Location / Link</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Zoom or Boardroom 3" 
                      className="form-input"
                      value={metadata.meetingLocation}
                      onChange={(e) => setMetadata({...metadata, meetingLocation: e.target.value})}
                    />
                  </div>
                  <div className="form-group">
                    <label>Participants</label>
                    <input 
                      type="text" 
                      placeholder="Names, comma separated (e.g. Kwame, Sarah, Abeo)" 
                      className="form-input"
                      value={metadata.meetingParticipants}
                      onChange={(e) => setMetadata({...metadata, meetingParticipants: e.target.value})}
                    />
                  </div>
                  <div className="form-group full-width">
                    <label>Purpose / Goals</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Align team on budget limits and hosting spaces" 
                      className="form-input"
                      value={metadata.meetingPurpose}
                      onChange={(e) => setMetadata({...metadata, meetingPurpose: e.target.value})}
                    />
                  </div>
                  <div className="form-group full-width">
                    <label>Agenda</label>
                    <textarea 
                      placeholder="e.g. 1. Budget limits review&#10;2. Deepgram credits update&#10;3. Free plan policy approval" 
                      className="form-textarea" 
                      rows="3"
                      value={metadata.meetingAgenda}
                      onChange={(e) => setMetadata({...metadata, meetingAgenda: e.target.value})}
                    />
                  </div>
                </div>
              )}

              {recordingType === "Memo/Voice Note" && (
                <div className="metadata-grid">
                  <div className="form-group full-width">
                    <label>Memo Title</label>
                    <input 
                      type="text" 
                      placeholder="e.g. Ideas on micro-animations and layouts" 
                      className="form-input" 
                      value={metadata.memoTitle}
                      onChange={(e) => setMetadata({...metadata, memoTitle: e.target.value})}
                    />
                  </div>
                  <div className="form-group full-width">
                    <label>Date</label>
                    <input 
                      type="date" 
                      className="form-input"
                      value={metadata.memoDate}
                      onChange={(e) => setMetadata({...metadata, memoDate: e.target.value})}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Action Buttons */}
            <div className="margin-top-lg" style={{ display: "flex", justifyContent: "flex-end" }}>
              <button 
                className="btn btn-primary" 
                disabled={!audioFile || isProcessing || status.user_is_over_limit}
                onClick={handleTranscribe}
              >
                {isProcessing ? "Transcribing..." : "Relax & Take Notes"} <ArrowRight size={18} />
              </button>
            </div>
          </div>

          {/* Processing overlay */}
          {isProcessing && (
            <div style={{ position: "fixed", top: 0, left: 0, width: "100%", height: "100%", background: "rgba(7, 11, 25, 0.9)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
              <div className="wave-container" style={{ height: "60px", gap: "6px" }}>
                <span className="wave-bar" style={{ width: "6px" }}></span>
                <span className="wave-bar" style={{ width: "6px" }}></span>
                <span className="wave-bar" style={{ width: "6px" }}></span>
                <span className="wave-bar" style={{ width: "6px" }}></span>
                <span className="wave-bar" style={{ width: "6px" }}></span>
                <span className="wave-bar" style={{ width: "6px" }}></span>
                <span className="wave-bar" style={{ width: "6px" }}></span>
              </div>
              <h2 style={{ fontFamily: "var(--font-heading)", color: "var(--text-primary)", marginTop: "24px" }}>Processing Transcript</h2>
              <p style={{ color: "var(--accent-teal)", marginTop: "8px", maxWidth: "400px", textAlign: "center" }}>{processingStep}</p>
            </div>
          )}
        </div>
      ) : (
        /* Staging Area Screen */
        <div className="fade-in">
          {/* Top Panel title */}
          <div className="flex-between" style={{ marginBottom: "24px" }}>
            <div>
              <span style={{ fontSize: "0.85rem", color: "var(--accent-teal)", textTransform: "uppercase", fontWeight: "700" }}>STAGING AREA</span>
              <h1 style={{ fontSize: "2rem" }}>{documentTitle}</h1>
            </div>
            <button className="btn btn-secondary" onClick={() => { setTranscript(""); setAudioFile(null); }}>
              Start New Note
            </button>
          </div>

          <div className="staging-container">
            {/* Left Panel: Editable Transcript */}
            <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: "600px" }}>
              <div className="flex-between" style={{ marginBottom: "16px" }}>
                <h2 style={{ fontSize: "1.25rem", color: "var(--text-primary)" }}>Diarized Transcript</h2>
                <span className="text-muted-small">Feel free to edit the text below</span>
              </div>
              <textarea 
                className="transcript-area" 
                value={transcript} 
                onChange={(e) => setTranscript(e.target.value)}
                style={{ flexGrow: 1 }}
              />
              
              {/* Downloads */}
              <div className="margin-top-md" style={{ borderTop: "1px solid var(--border-color)", paddingTop: "20px" }}>
                <label style={{ display: "block", marginBottom: "8px", fontSize: "0.9rem" }}>Download Document</label>
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  <button className="btn btn-secondary" onClick={downloadTXT}>
                    <Download size={14} /> TXT
                  </button>
                  <button className="btn btn-secondary" onClick={downloadWord}>
                    <Download size={14} /> Word (.doc)
                  </button>
                  <button className="btn btn-secondary" onClick={downloadPDF}>
                    <Download size={14} /> PDF
                  </button>
                </div>
              </div>
            </div>

            {/* Right Panel: AI Features & TTS */}
            <div className="card" style={{ display: "flex", flexDirection: "column" }}>
              {/* Tab Navigation */}
              <div className="tabs">
                <button 
                  className={`tab ${aiActiveTab === 'summary' ? 'active' : ''}`}
                  onClick={() => setAiActiveTab("summary")}
                >
                  Summary & Minutes
                </button>
                <button 
                  className={`tab ${aiActiveTab === 'insights' ? 'active' : ''}`}
                  onClick={() => setAiActiveTab("insights")}
                >
                  Insights & Actions
                </button>
                <button 
                  className={`tab ${aiActiveTab === 'translation' ? 'active' : ''}`}
                  onClick={() => setAiActiveTab("translation")}
                >
                  Translate
                </button>
                <button 
                  className={`tab ${aiActiveTab === 'tts' ? 'active' : ''}`}
                  onClick={() => setAiActiveTab("tts")}
                >
                  Voice (TTS)
                </button>
              </div>

              {/* Tab Contents */}
              <div style={{ flexGrow: 1, display: "flex", flexDirection: "column" }}>
                {aiActiveTab === "summary" && (
                  <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px" }}>
                    <p style={{ fontSize: "0.9rem" }}>Generate an executive summary and structured meeting minutes referencing timestamps and topic divisions.</p>
                    <button 
                      className="btn btn-primary" 
                      disabled={isAiLoading}
                      onClick={() => triggerAiFeature("summary")}
                    >
                      {isAiLoading ? "Processing..." : "Generate AI Summary"} <Sparkles size={16} />
                    </button>
                    <div className="ai-output-box">
                      {aiSummary ? (
                        <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiSummary) }} />
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>No summary generated yet. Click the button above to begin.</span>
                      )}
                    </div>
                  </div>
                )}

                {aiActiveTab === "insights" && (
                  <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px" }}>
                    <p style={{ fontSize: "0.9rem" }}>Extract actionable items, owner assignments, and strategic themes from the transcript.</p>
                    <button 
                      className="btn btn-primary" 
                      disabled={isAiLoading}
                      onClick={() => triggerAiFeature("insights")}
                    >
                      {isAiLoading ? "Processing..." : "Extract Insights"} <Sparkles size={16} />
                    </button>
                    <div className="ai-output-box">
                      {aiInsights ? (
                        <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiInsights) }} />
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>No insights extracted yet. Click the button above to begin.</span>
                      )}
                    </div>
                  </div>
                )}

                {aiActiveTab === "translation" && (
                  <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px" }}>
                    <p style={{ fontSize: "0.9rem" }}>Translate the speaker-diarized transcript to a foreign language while maintaining layout structure.</p>
                    
                    <div className="form-group" style={{ marginBottom: 0 }}>
                      <label>Target Language</label>
                      <select 
                        className="form-select"
                        value={targetLanguage}
                        onChange={(e) => setTargetLanguage(e.target.value)}
                      >
                        <option value="French">French (Français)</option>
                        <option value="Spanish">Spanish (Español)</option>
                        <option value="German">German (Deutsch)</option>
                        <option value="Portuguese">Portuguese (Português)</option>
                        <option value="Swahili">Swahili (Kiswahili)</option>
                        <option value="Arabic">Arabic (العربية)</option>
                        <option value="Yoruba">Yoruba</option>
                        <option value="Zulu">Zulu</option>
                      </select>
                    </div>

                    <button 
                      className="btn btn-primary" 
                      disabled={isAiLoading}
                      onClick={() => triggerAiFeature("translation")}
                    >
                      {isAiLoading ? "Translating..." : "Translate Transcript"} <Languages size={16} />
                    </button>
                    <div className="ai-output-box">
                      {aiTranslation ? (
                        <div style={{ whiteSpace: "pre-wrap" }}>{aiTranslation}</div>
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>No translation performed yet. Click the button above to begin.</span>
                      )}
                    </div>
                  </div>
                )}

                {aiActiveTab === "tts" && (
                  <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px" }}>
                    <p style={{ fontSize: "0.9rem" }}>Synthesize the transcript into speech using free Microsoft neural voices with different regional accents.</p>
                    
                    <div className="form-group" style={{ marginBottom: 0 }}>
                      <label>Voice / Accent Style</label>
                      <select 
                        className="form-select"
                        value={ttsVoice}
                        onChange={(e) => setTtsVoice(e.target.value)}
                      >
                        <option value="en-ZA-LeahNeural">English (South Africa) - Leah (Female)</option>
                        <option value="en-KE-AsiliaNeural">English (Kenya) - Asilia (Female)</option>
                        <option value="en-NG-AbeoNeural">English (Nigeria) - Abeo (Male)</option>
                        <option value="en-US-JennyNeural">English (US) - Jenny (Female)</option>
                        <option value="en-US-GuyNeural">English (US) - Guy (Male)</option>
                        <option value="en-GB-SoniaNeural">English (UK) - Sonia (Female)</option>
                        <option value="fr-FR-DeniseNeural">French (France) - Denise (Female)</option>
                        <option value="pt-BR-FranciscaNeural">Portuguese (Brazil) - Francisca (Female)</option>
                        <option value="sw-KE-RafikiNeural">Swahili (Kenya) - Rafiki (Male)</option>
                        <option value="sw-TZ-RehemaNeural">Swahili (Tanzania) - Rehema (Female)</option>
                      </select>
                    </div>

                    <button 
                      className="btn btn-accent" 
                      disabled={isTtsSynthesizing}
                      onClick={handleTtsSynthesis}
                    >
                      {isTtsSynthesizing ? "Synthesizing Voice..." : "Synthesize Voice"} <Volume2 size={16} />
                    </button>

                    {ttsAudioUrl && (
                      <div className="fade-in" style={{ background: "rgba(22, 34, 63, 0.4)", border: "1px solid var(--border-color)", padding: "16px", borderRadius: "var(--radius-md)", marginTop: "16px", display: "flex", flexDirection: "column", alignItems: "center" }}>
                        <span style={{ fontSize: "0.85rem", color: "var(--accent-teal)", fontWeight: "600", marginBottom: "8px" }}>AUDIO SYNTHESIS COMPLETE</span>
                        <audio 
                          ref={audioPlayerRef} 
                          src={ttsAudioUrl} 
                          controls 
                          style={{ width: "100%" }}
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
