-- schema.sql for relaxntakenotes.africa

-- Create usage_logs table to track API usage and enforce budget constraints
CREATE TABLE IF NOT EXISTS usage_logs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_hash TEXT NOT NULL,       -- SHA-256 hash of IP + User-Agent
  duration_seconds INTEGER NOT NULL, -- Length of audio transcribed in seconds
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Index user_hash and created_at to speed up limit checks
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_hash ON usage_logs(user_hash);
CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at ON usage_logs(created_at);

-- Set up Row Level Security (RLS) to restrict unauthorized direct access if needed,
-- but since the backend uses the service_role/private connection, it bypasses RLS.
-- We can add simple read-only public tracking or disable read access as needed.
ALTER TABLE usage_logs ENABLE ROW LEVEL SECURITY;

-- Allow read-only access for service role or authenticated users, or keep it restricted
CREATE POLICY "Allow backend full access" ON usage_logs
  FOR ALL
  USING (true)
  WITH CHECK (true);
