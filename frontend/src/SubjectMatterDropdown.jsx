import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Folder, Stethoscope, Scale, GraduationCap, TrendingUp, Check } from 'lucide-react';
import './SubjectMatterDropdown.css';

const OPTIONS = [
  { id: 'General', label: 'General Purpose', icon: Folder },
  { id: 'Medical', label: 'Medical / Clinical', icon: Stethoscope },
  { id: 'Legal', label: 'Legal', icon: Scale },
  { id: 'Education', label: 'Education', icon: GraduationCap },
  { id: 'Finance', label: 'Finance', icon: TrendingUp },
];

export default function SubjectMatterDropdown({ value, onChange }) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  const selectedOption = OPTIONS.find(opt => opt.id === value) || OPTIONS[0];
  const SelectedIcon = selectedOption.icon;

  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  return (
    <div className="smd-container" ref={dropdownRef}>
      <button 
        type="button" 
        className={`smd-trigger ${isOpen ? 'open' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="smd-selected-content">
          <SelectedIcon size={16} className="smd-icon" />
          <span>{selectedOption.label}</span>
        </div>
        <ChevronDown size={14} className="smd-chevron" />
      </button>

      {isOpen && (
        <div className="smd-menu">
          {OPTIONS.map((option) => {
            const Icon = option.icon;
            const isSelected = option.id === value;
            
            return (
              <button
                key={option.id}
                type="button"
                className={`smd-option ${isSelected ? 'selected' : ''}`}
                onClick={() => {
                  onChange(option.id);
                  setIsOpen(false);
                }}
              >
                <div className="smd-option-content">
                  <Icon size={16} className={`smd-icon ${isSelected ? 'selected-icon' : ''}`} />
                  <span>{option.label}</span>
                </div>
                {isSelected && <Check size={14} className="smd-check" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
