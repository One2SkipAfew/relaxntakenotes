import { useState } from 'react';
import { supabase } from './supabaseClient';
import { X, Mail, Lock, User, Building, MapPin, AlertCircle } from 'lucide-react';
import './AuthModal.css';

export default function AuthModal({ isOpen, onClose, defaultTab = 'signin' }) {
  const [tab, setTab] = useState(defaultTab); // 'signin' or 'register'
  
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [orgName, setOrgName] = useState('');
  const [orgAddress, setOrgAddress] = useState('');

  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  if (!isOpen) return null;

  const resetForm = () => {
    setEmail('');
    setPassword('');
    setConfirmPassword('');
    setFirstName('');
    setLastName('');
    setOrgName('');
    setOrgAddress('');
    setError('');
    setSuccessMsg('');
  };

  const switchTab = (newTab) => {
    setTab(newTab);
    resetForm();
  };

  const handleAuth = async (e) => {
    e.preventDefault();
    setError('');
    setSuccessMsg('');
    setLoading(true);

    try {
      if (tab === 'register') {
        if (password !== confirmPassword) {
          throw new Error("Passwords do not match");
        }
        if (!firstName || !lastName || !orgName) {
          throw new Error("Please fill in all required fields");
        }

        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: {
              first_name: firstName,
              last_name: lastName,
              organization_name: orgName,
              organization_address: orgAddress
            }
          }
        });

        if (signUpError) throw signUpError;
        
        setSuccessMsg('Registration successful! Please check your email to confirm your account.');
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email,
          password
        });

        if (signInError) throw signInError;
        
        onClose(); // Close modal on successful login
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-overlay" onClick={onClose}>
      <div className="auth-modal fade-in" onClick={(e) => e.stopPropagation()}>
        <button className="auth-close-btn" onClick={onClose}>
          <X size={20} />
        </button>

        <div className="auth-header">
          <h2>{tab === 'signin' ? 'Welcome Back' : 'Create an Account'}</h2>
          <p>
            {tab === 'signin' 
              ? 'Sign in to access your persistent organization documents and history.' 
              : 'Register to unlock persistent context documents for fact-checking.'}
          </p>
        </div>

        <div className="auth-tabs">
          <button 
            className={`auth-tab ${tab === 'signin' ? 'active' : ''}`}
            onClick={() => switchTab('signin')}
          >
            Sign In
          </button>
          <button 
            className={`auth-tab ${tab === 'register' ? 'active' : ''}`}
            onClick={() => switchTab('register')}
          >
            Register
          </button>
        </div>

        <form onSubmit={handleAuth} className="auth-form">
          {error && (
            <div className="auth-error">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}
          
          {successMsg && (
            <div className="auth-success">
              <span>{successMsg}</span>
            </div>
          )}

          {tab === 'register' && (
            <>
              <div className="auth-row">
                <div className="auth-input-group">
                  <User size={18} className="auth-icon" />
                  <input 
                    type="text" 
                    placeholder="First Name *" 
                    value={firstName} 
                    onChange={(e) => setFirstName(e.target.value)} 
                    required={tab === 'register'}
                  />
                </div>
                <div className="auth-input-group">
                  <User size={18} className="auth-icon" />
                  <input 
                    type="text" 
                    placeholder="Last Name *" 
                    value={lastName} 
                    onChange={(e) => setLastName(e.target.value)} 
                    required={tab === 'register'}
                  />
                </div>
              </div>

              <div className="auth-input-group">
                <Building size={18} className="auth-icon" />
                <input 
                  type="text" 
                  placeholder="Organization Name *" 
                  value={orgName} 
                  onChange={(e) => setOrgName(e.target.value)} 
                  required={tab === 'register'}
                />
              </div>

              <div className="auth-input-group">
                <MapPin size={18} className="auth-icon" />
                <input 
                  type="text" 
                  placeholder="Organization Address (Optional)" 
                  value={orgAddress} 
                  onChange={(e) => setOrgAddress(e.target.value)} 
                />
              </div>
            </>
          )}

          <div className="auth-input-group">
            <Mail size={18} className="auth-icon" />
            <input 
              type="email" 
              placeholder="Email Address *" 
              value={email} 
              onChange={(e) => setEmail(e.target.value)} 
              required
            />
          </div>

          <div className="auth-row">
            <div className="auth-input-group">
              <Lock size={18} className="auth-icon" />
              <input 
                type="password" 
                placeholder="Password *" 
                value={password} 
                onChange={(e) => setPassword(e.target.value)} 
                required
              />
            </div>
            {tab === 'register' && (
              <div className="auth-input-group">
                <Lock size={18} className="auth-icon" />
                <input 
                  type="password" 
                  placeholder="Confirm Password *" 
                  value={confirmPassword} 
                  onChange={(e) => setConfirmPassword(e.target.value)} 
                  required={tab === 'register'}
                />
              </div>
            )}
          </div>

          <button type="submit" className="auth-submit-btn" disabled={loading}>
            {loading ? 'Processing...' : (tab === 'signin' ? 'Sign In' : 'Create Account')}
          </button>
        </form>

      </div>
    </div>
  );
}
