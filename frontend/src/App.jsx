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
  Clock,
  Languages,
  TrendingUp,
  Cpu
} from "lucide-react";
import { jsPDF } from "jspdf";

// Import visual assets
import soundwaveAccent from "./assets/soundwave_accent.png";
import translationAccent from "./assets/translation_accent.png";
import processingAccent from "./assets/processing_accent.png";
import documentAccent from "./assets/document_accent.png";
import centerpieceImage from "./assets/centerpiece.png";

// Custom Africa Microphone Logo representation
const AfricaMicLogo = () => (
  <div className="logo-icon">
    <svg width="38" height="38" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginRight: '8px' }}>
      {/* Wave coming in from left */}
      <path d="M 4 10 A 10 10 0 0 1 4 30" stroke="#00F0FF" strokeWidth="2" strokeLinecap="round" opacity="0.3" />
      <path d="M 8 14 A 6 6 0 0 1 8 26" stroke="#00F0FF" strokeWidth="2" strokeLinecap="round" />

      {/* Africa Map shape as Mic body */}
      <path d="M 18.5 8 
               C 16.5 8, 14 10, 13.5 12.5 
               C 13 15, 14.5 17.5, 15 19 
               C 15.5 20, 16.8 22.5, 17.5 24 
               C 17.8 24.6, 18.2 24.8, 18.4 24.4
               C 19 22.8, 19.5 21.8, 20.2 21 
               C 21.2 20.2, 22.2 19, 22.5 17 
               C 22.8 15, 21.2 11, 20.5 9.5 
               C 20 8.5, 19.5 8, 18.5 8 Z"
        fill="rgba(0, 240, 255, 0.12)"
        stroke="#00F0FF"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      {/* Mic Stand */}
      <line x1="18" y1="25" x2="18" y2="31" stroke="#94A3B8" strokeWidth="2" />
      <path d="M 13 31 L 23 31" stroke="#94A3B8" strokeWidth="2" strokeLinecap="round" />

      {/* Data bits streaming out to the right */}
      <line x1="25" y1="12" x2="31" y2="12" stroke="#8A2BE2" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="34" cy="12" r="1.5" fill="#8A2BE2" />
      <line x1="26" y1="17" x2="32" y2="17" stroke="#8A2BE2" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="35" cy="17" r="1.5" fill="#8A2BE2" />
      <line x1="24" y1="22" x2="29" y2="22" stroke="#8A2BE2" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="32" cy="22" r="1.5" fill="#8A2BE2" />
    </svg>
  </div>
);

// Centerpiece Africa Image Representation with hard-blurred edges
const TechAfricaContinent = () => (
  <div className="centerpiece-container">
    <img src={centerpieceImage} alt="Centerpiece Icon" className="centerpiece-image" />
  </div>
);


const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
    return "http://localhost:7860";
  }
  return "";
};

const API_BASE_URL = getApiBaseUrl();

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

  const [audioFile, setAudioFile] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [dragOver, setDragOver] = useState(false);

  const [isProcessing, setIsProcessing] = useState(false);
  const [processingStep, setProcessingStep] = useState("");
  const [transcript, setTranscript] = useState("");
  const [documentTitle, setDocumentTitle] = useState("");

  const [aiActiveTab, setAiActiveTab] = useState("summary");
  const [aiSummary, setAiSummary] = useState("");
  const [aiInsights, setAiInsights] = useState("");
  const [aiTranslation, setAiTranslation] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("French");
  const [isAiLoading, setIsAiLoading] = useState(false);

  const [ttsVoice, setTtsVoice] = useState("en-ZA-LeahNeural");
  const [isTtsSynthesizing, setIsTtsSynthesizing] = useState(false);
  const [ttsAudioUrl, setTtsAudioUrl] = useState(null);

  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const recordingTimerRef = useRef(null);
  const audioPlayerRef = useRef(null);

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
        const file = new File([audioBlob], "recorded_audio.webm", { type: "audio/webm" });
        setAudioFile(file);
        stream.getTracks().forEach(track => track.stop());
      };

      mediaRecorderRef.current.start(250);
      setIsRecording(true);
      setRecordingDuration(0);

      recordingTimerRef.current = setInterval(() => {
        setRecordingDuration(prev => {
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

  const handleTranscribe = async () => {
    if (!audioFile) return;

    setIsProcessing(true);
    setProcessingStep("Uploading audio payload and initiating transcription...");
    setTranscript("");

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
      setProcessingStep("Transcribing audio and identifying speakers...");
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

      let transcriptText = "";
      if (data.paragraphs && data.paragraphs.length > 0) {
        transcriptText = data.paragraphs.map(p => `${p.speaker}: ${p.text}`).join("\n\n");
      } else {
        transcriptText = data.raw_transcript;
      }

      setTranscript(transcriptText);
      setIsProcessing(false);
      fetchStatus();

    } catch (err) {
      alert(`Error transcribing audio: ${err.message}`);
      setIsProcessing(false);
    }
  };

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

  const handleTtsSynthesis = async () => {
    if (!transcript) return;

    setIsTtsSynthesizing(true);
    if (ttsAudioUrl) {
      URL.revokeObjectURL(ttsAudioUrl);
      setTtsAudioUrl(null);
    }

    const cleanText = transcript.replace(/Speaker \d+:/g, "");

    try {
      const response = await fetch(`${API_BASE_URL}/api/tts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          text: cleanText.substring(0, 3000),
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

  const formatTime = (secs) => {
    const mins = Math.floor(secs / 60);
    const remainingSecs = secs % 60;
    return `${mins.toString().padStart(2, "0")}:${remainingSecs.toString().padStart(2, "0")}`;
  };

  const renderMarkdown = (md) => {
    if (!md) return "";
    let html = md;
    html = html.replace(/^### (.*$)/gim, '<h3 style="font-family: var(--font-heading); color: var(--accent-cyan); font-size: 0.9rem; margin: 16px 0 8px 0;">$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h2 style="font-family: var(--font-heading); color: var(--text-primary); font-size: 1.1rem; margin: 20px 0 10px 0;">$1</h2>');
    html = html.replace(/^# (.*$)/gim, '<h1 style="font-family: var(--font-heading); color: var(--text-primary); font-size: 1.3rem; margin: 24px 0 12px 0; border-bottom: 1px solid var(--border-color); padding-bottom: 4px;">$1</h1>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--accent-cyan); font-weight:600;">$1</strong>');
    html = html.replace(/^\s*[-*+]\s+(.*$)/gim, '<li style="margin-left: 16px; margin-bottom: 6px; list-style-type: square; color: var(--text-secondary); font-size: 0.85rem;">$1</li>');
    html = html.replace(/\n/g, '<br />');
    return html;
  };

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
    doc.text(documentTitle || "Relax n Take Notes Transcript", 14, 20);

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

  if (status.is_over_budget) {
    return (
      <div className="container fade-in">
        <div className="over-budget-container">
          <div className="over-budget-icon" style={{ textShadow: "0 0 30px var(--accent-cyan-glow)", color: "var(--accent-cyan)" }}>🎙️</div>
          <h1 style={{ fontSize: "2rem", marginBottom: "16px", color: "var(--accent-cyan)" }}>Server resting...</h1>
          <h2 style={{ fontSize: "1.2rem", marginBottom: "24px", color: "var(--text-primary)" }}>We've Hit the Monthly Ceiling!</h2>
          <p style={{ marginBottom: "32px", fontSize: "0.95rem" }}>
            The free server budget for transcriptions has been fully consumed for this month.
            We cap execution limits on relaxntakenotes.africa to keep our hosting free for everyone.
            We will be back online automatically next month.
          </p>
          <div style={{ display: "flex", gap: "16px" }}>
            <button className="btn btn-secondary" onClick={fetchStatus}>Check Again</button>
            <a href="mailto:support@relaxntakenotes.africa" className="btn btn-primary">Upgrade Platform</a>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <div style={{ flexGrow: 1 }}>

        {/* Section 1: Landing & Hero Section (Uses primary background) */}
        <section className="section-hero">
          <div className="container">
            {/* Header */}
            <header className="header" style={{ marginBottom: "4px" }}>
              <div className="logo" onClick={() => { setTranscript(""); setAudioFile(null); }}>
                <AfricaMicLogo />
                Relax n Take Notes
              </div>

              <div className="flex-center">
                <div className="budget-badge">
                  <Clock size={14} className="text-teal" />
                  <span>Used: {status.user_usage_minutes} / {status.user_limit_minutes} min (You)</span>
                </div>
              </div>
            </header>

            {/* Hero Copy Content */}
            <div style={{ textAlign: "center", maxWidth: "900px", margin: "0 auto" }}>
              {/* Technological Africa Continent Centerpiece */}
              <TechAfricaContinent />

              <h1 style={{ fontSize: "3.6rem", lineHeight: "1.1", marginBottom: "12px", background: "linear-gradient(135deg, #FFF 40%, var(--accent-cyan) 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
                Relax, I'll take notes.
              </h1>
              <h2 style={{ fontSize: "1.35rem", color: "var(--accent-cyan)", fontFamily: "var(--font-heading)", fontWeight: "600", marginTop: "0", marginBottom: "12px", letterSpacing: "0.01em" }}>
                Africa's AI Powered Speech-to-Text and Speech-to-Text-to-Speech Platform
              </h2>
              <p style={{ fontSize: "1.15rem", color: "var(--text-secondary)", lineHeight: "1.6", fontWeight: "500" }}>
                Let's take your voice recordings and turn them into text. Use features such as Summary and Insights, and others, to manage the information in the recording. It's safe, fast and free. Record and upload audio files, process the information, manage the information and export when ready. Just upload, process and download. It's that easy!
              </p>

              <div style={{ marginTop: "20px" }}>
                <button
                  className="btn btn-primary"
                  onClick={() => {
                    const el = document.getElementById("workspace");
                    if (el) {
                      el.scrollIntoView({ behavior: "smooth" });
                    }
                  }}
                  style={{ padding: "14px 28px", fontSize: "0.9rem", letterSpacing: "0.02em" }}
                >
                  Go to Note Synthesis Engine <ArrowRight size={16} />
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* Visual Page Break (Full-width divider) */}
        <div className="page-break" />

        {/* Section 2: Workflow & Blueprint Section (Uses shaded visual background) */}
        <section className="section-blueprint">
          <div className="container">
            <p className="section-subtitle">Workflow & Blueprint</p>
            <h2 className="section-title">Core Capabilities</h2>

            <div className="steps-grid">
              {/* Feature 1 */}
              <div className="step-card">
                <div className="step-icon-wrapper">
                  <Mic size={18} />
                </div>
                <h3 className="step-title">Capture Sound</h3>
                <p className="step-desc">
                  Record directly in your browser or drag-and-drop any audio file. We support MP3, WAV, M4A, and WebM.
                </p>
                <div
                  className="step-image-accent"
                  style={{ backgroundImage: `url(${soundwaveAccent})`, marginTop: "12px" }}
                />
              </div>

              {/* Feature 2 */}
              <div className="step-card">
                <div className="step-icon-wrapper">
                  <Cpu size={18} />
                </div>
                <h3 className="step-title">Fuel Context</h3>
                <p className="step-desc">
                  Select your category (meeting, song, memo) and fill in optional metadata context to feed the AI correct names and terms.
                </p>
                <div
                  className="step-image-accent"
                  style={{ backgroundImage: `url(${processingAccent})`, marginTop: "12px" }}
                />
              </div>

              {/* Feature 3 */}
              <div className="step-card">
                <div className="step-icon-wrapper">
                  <Languages size={18} />
                </div>
                <h3 className="step-title">AI Translation</h3>
                <p className="step-desc">
                  Diarize voices instantly. Summarize transcript findings, parse action lists, or translate to global and local African languages.
                </p>
                <div
                  className="step-image-accent"
                  style={{ backgroundImage: `url(${translationAccent})`, marginTop: "12px" }}
                />
              </div>

              {/* Feature 4 */}
              <div className="step-card">
                <div className="step-icon-wrapper">
                  <Volume2 size={18} />
                </div>
                <h3 className="step-title">Re-Voice & Export</h3>
                <p className="step-desc">
                  Synthesize summary text back into localized audio with accents and export directly to PDF, Word, or TXT formats.
                </p>
                <div
                  className="step-image-accent"
                  style={{ backgroundImage: `url(${documentAccent})`, marginTop: "12px" }}
                />
              </div>
            </div>
          </div>
        </section>

        {/* Visual Page Break (Full-width divider) */}
        <div className="page-break" />

        {/* Section 3: Transcription Widget Section */}
        <section className="section-widget" id="workspace">
          <div className="container">
            <p className="section-subtitle">Note Synthesis Engine</p>
            <h2 className="section-title">Recording & Audio Staging Workspace</h2>

            {!transcript ? (
              <div className="card" style={{ marginTop: "20px" }}>

                {/* Notification Alert box */}
                <div className="notification-card" style={{ marginBottom: "20px" }}>
                  <div className="notification-badge-icon">
                    <AlertTriangle size={16} />
                  </div>
                  <div>
                    <strong style={{ color: "var(--accent-purple)", fontSize: "0.7rem", fontFamily: "var(--font-heading)" }}>RESOURCE CONSTRAINTS LOG</strong>
                    <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                      Recordings are limited to {status.max_recording_duration_minutes} minutes each. Personal monthly usage is capped at {status.user_limit_minutes} minutes. Once exceeded, your profile UUID locks until the monthly budget cycle resets.
                    </p>
                  </div>
                </div>

                {/* Split Grid Layout */}
                <div className="widget-grid">

                  {/* Left Column: Category & Audio source upload */}
                  <div className="widget-left-pane">
                    <div className="form-group">
                      <label>Recording Category</label>
                      <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                        <button
                          className={`btn ${recordingType === "Meeting/Hearing" ? 'btn-primary' : 'btn-secondary'}`}
                          onClick={() => setRecordingType("Meeting/Hearing")}
                          style={{ justifyContent: "flex-start" }}
                        >
                          <Users size={14} /> Meeting or Hearing
                        </button>
                        <button
                          className={`btn ${recordingType === "Song" ? 'btn-primary' : 'btn-secondary'}`}
                          onClick={() => setRecordingType("Song")}
                          style={{ justifyContent: "flex-start" }}
                        >
                          <Music size={14} /> Song / Music Lyrics
                        </button>
                        <button
                          className={`btn ${recordingType === "Memo/Voice Note" ? 'btn-primary' : 'btn-secondary'}`}
                          onClick={() => setRecordingType("Memo/Voice Note")}
                          style={{ justifyContent: "flex-start" }}
                        >
                          <FileText size={14} /> Memo or Voice Note
                        </button>
                      </div>
                    </div>

                    <div className="form-group" style={{ flexGrow: 1, display: "flex", flexDirection: "column" }}>
                      <label>Audio Source</label>

                      {!audioFile && !isRecording ? (
                        <div className="audio-source-options">
                          {/* Option 1: Record Live */}
                          <div
                            className="audio-option-btn btn-record"
                            onClick={startRecording}
                          >
                            <Mic size={22} style={{ marginBottom: "4px" }} />
                            <span style={{ fontSize: "0.85rem", fontWeight: "700" }}>Record Live</span>
                            <span className="text-muted-small" style={{ fontSize: "0.65rem", marginTop: "2px" }}>Capture from microphone</span>
                          </div>

                          {/* Option 2: Upload File */}
                          <div
                            className="audio-option-btn btn-upload"
                            onClick={() => document.getElementById("file-input").click()}
                            onDragOver={handleDragOver}
                            onDragLeave={handleDragLeave}
                            onDrop={handleDrop}
                          >
                            <Upload size={22} style={{ marginBottom: "4px" }} />
                            <span style={{ fontSize: "0.85rem", fontWeight: "700" }}>Upload Audio</span>
                            <span className="text-muted-small" style={{ fontSize: "0.65rem", marginTop: "2px" }}>Drag & drop or browse</span>
                            <input
                              id="file-input"
                              type="file"
                              accept="audio/*"
                              style={{ display: "none" }}
                              onChange={handleFileSelect}
                            />
                          </div>
                        </div>
                      ) : isRecording ? (
                        <div className="recording-widget" style={{ borderColor: "var(--error)", background: "rgba(239, 68, 68, 0.01)", flexGrow: 1 }}>
                          <button className="mic-button recording" onClick={stopRecording}>
                            <Square size={16} />
                          </button>
                          <h3 style={{ color: "var(--error)", marginBottom: "2px", fontSize: "0.8rem" }}>Recording Live</h3>
                          <div className="wave-container">
                            <span className="wave-bar"></span>
                            <span className="wave-bar"></span>
                            <span className="wave-bar"></span>
                            <span className="wave-bar"></span>
                            <span className="wave-bar"></span>
                          </div>
                          <div style={{ display: "flex", alignItems: "center", gap: "5px", justifyContent: "center", fontFamily: "var(--font-heading)", fontSize: "1rem", fontWeight: "700" }}>
                            <Clock size={12} className="text-error" />
                            <span>{formatTime(recordingDuration)}</span>
                          </div>
                          <button
                            className="btn btn-secondary margin-top-md"
                            onClick={() => { stopRecording(); setAudioFile(null); }}
                            style={{ padding: "4px 10px", fontSize: "0.7rem" }}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="recording-widget" style={{ borderColor: "var(--accent-cyan)", flexGrow: 1 }}>
                          <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "10px" }}>
                            <FileAudio size={28} className="text-teal" />
                            <div style={{ textAlign: "left" }}>
                              <h4 style={{ wordBreak: "break-all", fontSize: "0.75rem" }}>{audioFile.name}</h4>
                              <p className="text-muted-small">Size: {(audioFile.size / 1024 / 1024).toFixed(2)} MB</p>
                            </div>
                          </div>
                          <div style={{ display: "flex", gap: "6px" }}>
                            <button className="btn btn-secondary" onClick={() => setAudioFile(null)} style={{ padding: "4px 8px", fontSize: "0.7rem" }}>
                              <RotateCcw size={10} /> Clear
                            </button>
                            <label className="btn btn-secondary" htmlFor="file-input-change" style={{ cursor: "pointer", padding: "4px 8px", fontSize: "0.7rem" }}>
                              <Upload size={10} /> Replace
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

                    {/* Action Button right under upload widget */}
                    <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
                      <button
                        className="btn btn-primary"
                        disabled={!audioFile || isProcessing || status.user_is_over_limit}
                        onClick={handleTranscribe}
                        style={{ width: "100%", padding: "12px" }}
                      >
                        {isProcessing ? "Processing..." : "Start Transcribing"} <ArrowRight size={14} />
                      </button>
                    </div>
                  </div>

                  {/* Right Column: Dynamic Metadata Context */}
                  <div className="widget-right-pane">
                    <h3 style={{ marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px", fontSize: "0.85rem" }}>
                      <span>Inject Context Variables</span>
                      <span className="text-muted-small">(Optional)</span>
                    </h3>

                    {recordingType === "Song" && (
                      <div className="metadata-grid" style={{ marginTop: 0 }}>
                        <div className="form-group">
                          <label>Artist Name</label>
                          <input
                            type="text"
                            placeholder="e.g. Miriam Makeba"
                            className="form-input"
                            value={metadata.songArtist}
                            onChange={(e) => setMetadata({ ...metadata, songArtist: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Song Title</label>
                          <input
                            type="text"
                            placeholder="e.g. Qongqothwane"
                            className="form-input"
                            value={metadata.songTitle}
                            onChange={(e) => setMetadata({ ...metadata, songTitle: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Recording Location</label>
                          <input
                            type="text"
                            placeholder="e.g. Johannesburg Studio"
                            className="form-input"
                            value={metadata.songLocation}
                            onChange={(e) => setMetadata({ ...metadata, songLocation: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Release Date</label>
                          <input
                            type="date"
                            className="form-input"
                            value={metadata.songDate}
                            onChange={(e) => setMetadata({ ...metadata, songDate: e.target.value })}
                          />
                        </div>
                      </div>
                    )}

                    {recordingType === "Meeting/Hearing" && (
                      <div className="metadata-grid" style={{ marginTop: 0 }}>
                        <div className="form-group full-width">
                          <label>Meeting Title</label>
                          <input
                            type="text"
                            placeholder="e.g. Q2 Architecture and Hosting Alignment"
                            className="form-input"
                            value={metadata.meetingTitle}
                            onChange={(e) => setMetadata({ ...metadata, meetingTitle: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Meeting Date</label>
                          <input
                            type="date"
                            className="form-input"
                            value={metadata.meetingDate}
                            onChange={(e) => setMetadata({ ...metadata, meetingDate: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Time</label>
                          <input
                            type="time"
                            className="form-input"
                            value={metadata.meetingTime}
                            onChange={(e) => setMetadata({ ...metadata, meetingTime: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Location / Call Link</label>
                          <input
                            type="text"
                            placeholder="e.g. Google Meet / Cape Town Room"
                            className="form-input"
                            value={metadata.meetingLocation}
                            onChange={(e) => setMetadata({ ...metadata, meetingLocation: e.target.value })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Participants</label>
                          <input
                            type="text"
                            placeholder="Names (Kwame, Sarah, Abeo)"
                            className="form-input"
                            value={metadata.meetingParticipants}
                            onChange={(e) => setMetadata({ ...metadata, meetingParticipants: e.target.value })}
                          />
                        </div>
                        <div className="form-group full-width">
                          <label>Purpose / Primary Goals</label>
                          <input
                            type="text"
                            placeholder="e.g. Approve limits and confirm space"
                            className="form-input"
                            value={metadata.meetingPurpose}
                            onChange={(e) => setMetadata({ ...metadata, meetingPurpose: e.target.value })}
                          />
                        </div>
                        <div className="form-group full-width" style={{ marginBottom: 0 }}>
                          <label>Agenda</label>
                          <textarea
                            placeholder="e.g. 1. Review project requirements&#10;2. Design system adjustments&#10;3. Final verification checks"
                            className="form-textarea"
                            rows="2"
                            value={metadata.meetingAgenda}
                            onChange={(e) => setMetadata({ ...metadata, meetingAgenda: e.target.value })}
                          />
                        </div>
                      </div>
                    )}

                    {recordingType === "Memo/Voice Note" && (
                      <div className="metadata-grid" style={{ marginTop: 0 }}>
                        <div className="form-group full-width">
                          <label>Memo Title</label>
                          <input
                            type="text"
                            placeholder="e.g. Ideas on cold dark-mode colors and gradients"
                            className="form-input"
                            value={metadata.memoTitle}
                            onChange={(e) => setMetadata({ ...metadata, memoTitle: e.target.value })}
                          />
                        </div>
                        <div className="form-group full-width" style={{ marginBottom: 0 }}>
                          <label>Date</label>
                          <input
                            type="date"
                            className="form-input"
                            value={metadata.memoDate}
                            onChange={(e) => setMetadata({ ...metadata, memoDate: e.target.value })}
                          />
                        </div>
                      </div>
                    )}
                  </div>

                </div>

              </div>
            ) : (
              /* Staging Area Workspace */
              <div className="staging-container">
                {/* Left Panel: Editable Transcript */}
                <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: "550px" }}>
                  <div className="flex-between" style={{ marginBottom: "12px" }}>
                    <h2 style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>Diarized Transcript</h2>
                    <span className="text-muted-small">Edit text blocks freely below</span>
                  </div>
                  <textarea
                    className="transcript-area"
                    value={transcript}
                    onChange={(e) => setTranscript(e.target.value)}
                    style={{ flexGrow: 1 }}
                  />

                  {/* Document Downloads */}
                  <div className="margin-top-md" style={{ borderTop: "1px solid var(--border-color)", paddingTop: "16px" }}>
                    <label style={{ display: "block", marginBottom: "8px", fontSize: "0.7rem", fontFamily: "var(--font-heading)", color: "var(--text-secondary)" }}>EXPORT DOCUMENT</label>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                      <button className="btn btn-secondary" onClick={downloadTXT} style={{ padding: "6px 12px", fontSize: "0.75rem" }}>
                        <Download size={12} /> Plain Text
                      </button>
                      <button className="btn btn-secondary" onClick={downloadWord} style={{ padding: "6px 12px", fontSize: "0.75rem" }}>
                        <Download size={12} /> MS Word (.doc)
                      </button>
                      <button className="btn btn-secondary" onClick={downloadPDF} style={{ padding: "6px 12px", fontSize: "0.75rem" }}>
                        <Download size={12} /> Adobe PDF
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
                      Summary
                    </button>
                    <button
                      className={`tab ${aiActiveTab === 'insights' ? 'active' : ''}`}
                      onClick={() => setAiActiveTab("insights")}
                    >
                      Insights
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
                      <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
                        <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                          Synthesize the transcript into a beautiful markdown executive summary and chronologically organized meeting minutes.
                        </p>
                        <button
                          className="btn btn-primary"
                          disabled={isAiLoading}
                          onClick={() => triggerAiFeature("summary")}
                        >
                          {isAiLoading ? "Processing..." : "Generate AI Summary"} <Sparkles size={12} />
                        </button>
                        <div className="ai-output-box">
                          {aiSummary ? (
                            <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiSummary) }} />
                          ) : (
                            <span style={{ color: "var(--text-muted)", fontStyle: "italic", fontSize: "0.75rem" }}>No summary generated yet. Click the button above.</span>
                          )}
                        </div>
                      </div>
                    )}

                    {aiActiveTab === "insights" && (
                      <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
                        <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                          Analyze discussion nodes to parse specific task assignments, action items, target owners, and primary themes.
                        </p>
                        <button
                          className="btn btn-primary"
                          disabled={isAiLoading}
                          onClick={() => triggerAiFeature("insights")}
                        >
                          {isAiLoading ? "Analyzing..." : "Extract Action Items"} <Sparkles size={12} />
                        </button>
                        <div className="ai-output-box">
                          {aiInsights ? (
                            <div dangerouslySetInnerHTML={{ __html: renderMarkdown(aiInsights) }} />
                          ) : (
                            <span style={{ color: "var(--text-muted)", fontStyle: "italic", fontSize: "0.75rem" }}>No action items parsed yet. Click the button above.</span>
                          )}
                        </div>
                      </div>
                    )}

                    {aiActiveTab === "translation" && (
                      <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
                        <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                          Translate full speaker transcripts into global and regional African languages while maintaining speaker lines.
                        </p>

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
                            <option value="Zulu">Zulu (isiZulu)</option>
                            <option value="Xhosa">Xhosa (isiXhosa)</option>
                          </select>
                        </div>

                        <button
                          className="btn btn-primary"
                          disabled={isAiLoading}
                          onClick={() => triggerAiFeature("translation")}
                        >
                          {isAiLoading ? "Translating..." : "Translate Transcript"} <Languages size={12} />
                        </button>
                        <div className="ai-output-box">
                          {aiTranslation ? (
                            <div style={{ whiteSpace: "pre-wrap", fontSize: "0.75rem" }}>{aiTranslation}</div>
                          ) : (
                            <span style={{ color: "var(--text-muted)", fontStyle: "italic", fontSize: "0.75rem" }}>No translation performed yet. Click the button above.</span>
                          )}
                        </div>
                      </div>
                    )}

                    {aiActiveTab === "tts" && (
                      <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
                        <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                          Re-voice the summary back into high-fidelity neural audio. Select accent characters for localized playback.
                        </p>

                        <div className="form-group" style={{ marginBottom: 0 }}>
                          <label>Voice Synthesis Accent</label>
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
                          {isTtsSynthesizing ? "Synthesizing Audio..." : "Synthesize voice"} <Volume2 size={12} />
                        </button>

                        {ttsAudioUrl && (
                          <div className="fade-in" style={{ background: "rgba(4, 6, 14, 0.6)", border: "1px solid var(--border-color)", padding: "12px", borderRadius: "var(--radius-md)", marginTop: "12px", display: "flex", flexDirection: "column", alignItems: "center" }}>
                            <span style={{ fontSize: "0.65rem", color: "var(--accent-cyan)", fontWeight: "700", marginBottom: "8px", fontFamily: "var(--font-heading)" }}>SYNTHESIS ACTIVE</span>
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
            )}

            {/* Visual Page Break */}
          </div>
        </section>

        {/* Visual Page Break (Full-width divider) */}
        <div className="page-break" />
      </div>

      {/* Footer Section */}
      <footer className="footer">
        <div className="container">
          <div className="footer-grid">
            <div className="footer-column">
              <div className="footer-logo" style={{ display: "flex", alignItems: "center" }}>
                <AfricaMicLogo />
                Relax n Take Notes
              </div>
              <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", lineHeight: "1.4", maxWidth: "450px" }}>
                AI-powered note-taking and high-speed audio transcription built for the people, by the people.
              </p>
            </div>
            <div className="footer-column">
              <h4>Platform Limits</h4>
              <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)", lineHeight: "1.4" }}>
                Quota caps checked live. Cap is 60 minutes per user and 3,500 minutes server-wide monthly.
              </p>
            </div>
            <div className="footer-column">
              <h4>Legal & Support</h4>
              <ul style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <li><a href="#terms">Terms and Conditions</a></li>
                <li><a href="#privacy">Privacy Policy</a></li>
                <li><a href="mailto:support@relaxntakenotes.africa">Contact Us</a></li>
              </ul>
            </div>
          </div>
          <div className="footer-bottom">
            <div>
              &copy; {new Date().getFullYear()} <strong>From B 2 C (pty) Ltd.</strong> All rights reserved.
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>
              relaxntakenotes.africa
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
