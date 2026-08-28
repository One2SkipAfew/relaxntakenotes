-- schema.sql for relaxntakenotes.africa

-- Create usage_logs table to track API usage and enforce budget constraints
CREATE TABLE IF NOT EXISTS usage_logs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_hash TEXT,       -- SHA-256 hash of IP + User-Agent (null for rows attributed via user_id only, e.g. livestream)
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL, -- Set when the request was made by a logged-in user
  duration_seconds INTEGER NOT NULL, -- Length of audio transcribed in seconds
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Relax legacy NOT NULL constraint on user_hash for pre-existing databases
ALTER TABLE usage_logs ALTER COLUMN user_hash DROP NOT NULL;

-- Add user_id to pre-existing databases that already had usage_logs without it
ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL;

-- Index user_hash, user_id and created_at to speed up limit checks / stats lookups
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_hash ON usage_logs(user_hash);
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_id ON usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at ON usage_logs(created_at);

-- Set up Row Level Security (RLS) to restrict unauthorized direct access if needed,
-- but since the backend uses the service_role/private connection, it bypasses RLS.
-- We can add simple read-only public tracking or disable read access as needed.
ALTER TABLE usage_logs ENABLE ROW LEVEL SECURITY;

-- Allow read-only access for service role or authenticated users, or keep it restricted
DROP POLICY IF EXISTS "Allow backend full access" ON usage_logs;
CREATE POLICY "Allow backend full access" ON usage_logs
  FOR ALL
  USING (true)
  WITH CHECK (true);

-- ==============================================================================
-- PHASE 2: Authentication & LiveStream Persistence
-- ==============================================================================

-- 1. Profiles Table (extends auth.users with custom fields)
CREATE TABLE IF NOT EXISTS profiles (
  id UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  organization_name TEXT,
  organization_address TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own profile" ON profiles;
CREATE POLICY "Users can view own profile" ON profiles
  FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own profile" ON profiles;
CREATE POLICY "Users can update own profile" ON profiles
  FOR UPDATE USING (auth.uid() = id);

DROP POLICY IF EXISTS "Backend can manage all profiles" ON profiles;
CREATE POLICY "Backend can manage all profiles" ON profiles
  FOR ALL USING (true) WITH CHECK (true);

-- 2. Context Documents (stored by users for fact-checking context)
CREATE TABLE IF NOT EXISTS context_documents (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  file_name TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  content_type TEXT,
  size_bytes BIGINT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE context_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage own documents" ON context_documents;
CREATE POLICY "Users can manage own documents" ON context_documents
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Backend can read all documents" ON context_documents;
CREATE POLICY "Backend can read all documents" ON context_documents
  FOR SELECT USING (true);

-- 3. LiveStream Sessions
CREATE TABLE IF NOT EXISTS livestream_sessions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL, -- Null if anonymous/guest
  title TEXT,
  transcript_text TEXT,
  ai_notes TEXT,
  meeting_package TEXT,
  audio_path TEXT,
  duration_seconds INTEGER,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE livestream_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own sessions" ON livestream_sessions;
CREATE POLICY "Users can view own sessions" ON livestream_sessions
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Backend can manage all sessions" ON livestream_sessions;
CREATE POLICY "Backend can manage all sessions" ON livestream_sessions
  FOR ALL USING (true) WITH CHECK (true);

-- 4. LiveStream Claims
CREATE TABLE IF NOT EXISTS livestream_claims (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  session_id UUID REFERENCES livestream_sessions(id) ON DELETE CASCADE NOT NULL,
  claim_text TEXT NOT NULL,
  speaker TEXT,
  category TEXT,
  verdict TEXT NOT NULL,
  confidence NUMERIC(4,3),
  explanation TEXT,
  key_evidence TEXT,
  sources JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE livestream_claims ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view claims for their sessions" ON livestream_claims;
CREATE POLICY "Users can view claims for their sessions" ON livestream_claims
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM livestream_sessions
      WHERE livestream_sessions.id = livestream_claims.session_id
      AND livestream_sessions.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Backend can manage all claims" ON livestream_claims;
CREATE POLICY "Backend can manage all claims" ON livestream_claims
  FOR ALL USING (true) WITH CHECK (true);

-- Function to handle new user signups and create a profile automatically
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, first_name, last_name, organization_name, organization_address)
  VALUES (
    new.id,
    new.raw_user_meta_data->>'first_name',
    new.raw_user_meta_data->>'last_name',
    new.raw_user_meta_data->>'organization_name',
    new.raw_user_meta_data->>'organization_address'
  );
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger for new user signup
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE public.handle_new_user();

-- Function to limit saved sessions to 2 per user
CREATE OR REPLACE FUNCTION public.limit_saved_sessions()
RETURNS trigger AS $$
BEGIN
  IF new.user_id IS NOT NULL THEN
    -- Delete the oldest sessions if the count exceeds 1 (so after insert it'll be at most 2)
    -- Actually, it's easier to just delete any session that is not the 2 most recent for this user.
    -- We can run this AFTER INSERT.
    DELETE FROM public.livestream_sessions
    WHERE user_id = new.user_id
      AND id NOT IN (
        SELECT id FROM public.livestream_sessions
        WHERE user_id = new.user_id
        ORDER BY created_at DESC
        LIMIT 2
      );
  END IF;
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_session_created ON livestream_sessions;
CREATE TRIGGER on_session_created
  AFTER INSERT ON livestream_sessions
  FOR EACH ROW EXECUTE PROCEDURE public.limit_saved_sessions();

-- Function to log livestream session duration into usage_logs so it counts toward
-- a user's lifetime dashboard stats, independent of the 2-session retention cap above.
CREATE OR REPLACE FUNCTION public.log_livestream_usage()
RETURNS trigger AS $$
BEGIN
  IF new.user_id IS NOT NULL AND new.duration_seconds IS NOT NULL THEN
    INSERT INTO public.usage_logs (user_id, duration_seconds)
    VALUES (new.user_id, new.duration_seconds);
  END IF;
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_livestream_session_usage ON livestream_sessions;
CREATE TRIGGER on_livestream_session_usage
  AFTER INSERT ON livestream_sessions
  FOR EACH ROW EXECUTE PROCEDURE public.log_livestream_usage();

-- ==============================================================================
-- PHASE 3: Vector RAG for Support Documents
-- ==============================================================================

-- Enable pgvector for embedding storage + similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Chunked, embedded text for each uploaded context document — powers real
-- retrieval (vs. dumping whole documents into prompts) for fact-check cross-referencing.
CREATE TABLE IF NOT EXISTS document_chunks (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  document_id UUID REFERENCES context_documents(id) ON DELETE CASCADE NOT NULL,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  embedding vector(384), -- sentence-transformers/all-MiniLM-L6-v2 dimension
  created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_user_id ON document_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id);

-- Cosine-similarity index for fast nearest-neighbor search
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding ON document_chunks
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage own chunks" ON document_chunks;
CREATE POLICY "Users can manage own chunks" ON document_chunks
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Backend can manage all chunks" ON document_chunks;
CREATE POLICY "Backend can manage all chunks" ON document_chunks
  FOR ALL USING (true) WITH CHECK (true);

-- Similarity search RPC — PostgREST can't evaluate the <=> operator directly,
-- so retrieval is wrapped in a Postgres function and called via .rpc(...).
CREATE OR REPLACE FUNCTION public.match_document_chunks(
  query_embedding vector(384),
  match_user_id UUID,
  match_count INT DEFAULT 5
)
RETURNS TABLE (
  chunk_text TEXT,
  file_name TEXT,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    dc.chunk_text,
    cd.file_name,
    1 - (dc.embedding <=> query_embedding) AS similarity
  FROM document_chunks dc
  JOIN context_documents cd ON cd.id = dc.document_id
  WHERE dc.user_id = match_user_id
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
