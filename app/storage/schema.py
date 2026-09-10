"""SQLite schema definitions."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id
ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS inbound_platform_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    message_type TEXT NOT NULL,
    text TEXT NOT NULL,
    parts_json TEXT NOT NULL DEFAULT '[]',
    is_self INTEGER NOT NULL CHECK (is_self IN (0, 1)),
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, account_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_messages_conversation
ON inbound_platform_messages (platform, account_id, conversation_id, id);

CREATE TABLE IF NOT EXISTS image_descriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, account_id, message_id, part_index)
);

CREATE TABLE IF NOT EXISTS qq_training_group_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    text TEXT NOT NULL,
    parts_json TEXT NOT NULL,
    is_self INTEGER NOT NULL CHECK (is_self IN (0, 1)),
    reply_to_message_id TEXT,
    nickname TEXT,
    source_type TEXT NOT NULL CHECK (
        source_type IN ('other_user', 'human_self', 'ai_generated')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, account_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_qq_training_group_context
ON qq_training_group_events (platform, account_id, group_id, id);

CREATE TABLE IF NOT EXISTS style_samples (
    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    conversation_type TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    context_messages TEXT NOT NULL,
    human_reply_message_id TEXT NOT NULL,
    human_reply TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (
        status IN ('candidate', 'accepted', 'rejected')
    ),
    UNIQUE (platform, conversation_id, human_reply_message_id)
);

CREATE INDEX IF NOT EXISTS idx_style_samples_status
ON style_samples (status, sample_id);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    content TEXT NOT NULL,
    start_message_id INTEGER NOT NULL,
    end_message_id INTEGER NOT NULL,
    previous_summary_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (start_message_id <= end_message_id),
    FOREIGN KEY (previous_summary_id) REFERENCES conversation_summaries (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_conversation_end
ON conversation_summaries (conversation_id, end_message_id);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (
        memory_type IN ('fact', 'preference', 'relationship', 'ongoing_event')
    ),
    memory_key TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source_message_ids TEXT NOT NULL,
    source_quote TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'ignore')),
    target_memory_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'accepted', 'rejected', 'ignored')
    ),
    rejection_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidates_conversation_status
ON memory_candidates (conversation_id, status, id);

CREATE TABLE IF NOT EXISTS long_term_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (
        memory_type IN ('fact', 'preference', 'relationship', 'ongoing_event')
    ),
    memory_key TEXT NOT NULL,
    content TEXT NOT NULL,
    source_start_message_id INTEGER NOT NULL,
    source_end_message_id INTEGER NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'superseded', 'disabled')
    ),
    supersedes_memory_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    CHECK (source_start_message_id <= source_end_message_id),
    FOREIGN KEY (supersedes_memory_id) REFERENCES long_term_memories (id)
);

CREATE INDEX IF NOT EXISTS idx_memories_conversation_status
ON long_term_memories (conversation_id, status, memory_type, updated_at);

CREATE INDEX IF NOT EXISTS idx_memories_key
ON long_term_memories (conversation_id, memory_type, memory_key, status);

CREATE TABLE IF NOT EXISTS contact_profiles (
    contact_id TEXT PRIMARY KEY,
    preferred_name TEXT,
    relationship TEXT,
    communication_style TEXT,
    stable_preferences TEXT,
    notes TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS current_account_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    public_name TEXT,
    background TEXT,
    public_information TEXT,
    private_information TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_processing_state (
    conversation_id TEXT PRIMARY KEY,
    last_processed_message_id INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
