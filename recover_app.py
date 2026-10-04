import json
import sys

log_path = r'C:\Users\kmthu\.gemini\antigravity-ide\brain\696cb2f5-3ec0-4dad-a506-65a07ee44539\.system_generated\logs\transcript_full.jsonl'
file_path = r'C:\Users\kmthu\projects\relaxntakenotes\frontend\src\App.jsx'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Load chunks from logs
chunks = []
with open(log_path, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            data = json.loads(line)
            if 'tool_calls' in data:
                for tc in data['tool_calls']:
                    if tc['name'] in ['replace_file_content', 'multi_replace_file_content'] and 'App.jsx' in str(tc['args']):
                        step = data['step_index']
                        if step in [79, 211, 222]:
                            if tc['name'] == 'replace_file_content':
                                chunks.append(tc['args'])
                            elif tc['name'] == 'multi_replace_file_content':
                                chunks.extend(tc['args']['ReplacementChunks'])
        except Exception as e:
            pass

# Apply chunks strictly by TargetContent replace
for chunk in chunks:
    target = chunk['TargetContent'].replace('\r\n', '\n')
    replacement = chunk['ReplacementContent'].replace('\r\n', '\n')
    
    # Normalize file content to \n for matching
    content = content.replace('\r\n', '\n')
    
    if target in content:
        content = content.replace(target, replacement, 1)
        print(f"Successfully applied chunk for target ending in: {target[-20:]}")
    else:
        print(f"FAILED to find target ending in: {target[-20:]}")

# Now manually apply the AuthModal fix reliably:
# 1. wrap in <>
content = content.replace(
    '  return (\n    <div className="fade-in"',
    '  return (\n    <>\n      <div className="fade-in"'
)

# 2. remove inner AuthModal
inner_modal = """        {/* Auth Modal */}
        <AuthModal 
          isOpen={isAuthModalOpen} 
          onClose={() => setIsAuthModalOpen(false)} 
          defaultTab={authModalTab}
        />"""
content = content.replace(inner_modal, '        {/* Auth Modal moved to root */}')

# 3. Add to bottom and close fragment
bottom_marker = """      <NotificationModal
        isOpen={!!notification}
        onClose={closeNotification}
        type={notification?.type}
        title={notification?.title}
        message={notification?.message}
        actions={notification?.actions}
      />
    </div>
  );"""

bottom_replacement = """      <NotificationModal
        isOpen={!!notification}
        onClose={closeNotification}
        type={notification?.type}
        title={notification?.title}
        message={notification?.message}
        actions={notification?.actions}
      />
    </div>

    {/* Auth Modal rendered at root level */}
    <AuthModal 
      isOpen={isAuthModalOpen} 
      onClose={() => setIsAuthModalOpen(false)} 
      defaultTab={authModalTab}
    />
  </>
  );"""
content = content.replace(bottom_marker, bottom_replacement)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated App.jsx successfully.")
