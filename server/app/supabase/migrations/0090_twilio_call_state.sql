-- 0090_twilio_call_state.sql
-- Durable Twilio call-state/event tracking for dialogmachine and voice reliability.

CREATE TABLE IF NOT EXISTS twilio_calls (
    call_sid TEXT PRIMARY KEY,
    call_id TEXT,
    companion_id UUID REFERENCES companions(id) ON DELETE SET NULL,
    relationship_id UUID REFERENCES relationships(id) ON DELETE SET NULL,
    external_user_id TEXT,
    direction TEXT,
    from_number TEXT,
    to_number TEXT,
    status TEXT,
    duration_seconds INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT twilio_calls_direction_check
        CHECK (direction IS NULL OR direction IN ('inbound', 'outbound'))
);

CREATE INDEX IF NOT EXISTS idx_twilio_calls_relationship_id
    ON twilio_calls(relationship_id);

CREATE INDEX IF NOT EXISTS idx_twilio_calls_companion_id
    ON twilio_calls(companion_id);

CREATE INDEX IF NOT EXISTS idx_twilio_calls_updated_at
    ON twilio_calls(updated_at DESC);

CREATE TABLE IF NOT EXISTS twilio_call_events (
    id BIGSERIAL PRIMARY KEY,
    call_sid TEXT NOT NULL REFERENCES twilio_calls(call_sid) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    status TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_twilio_call_events_call_sid_created_at
    ON twilio_call_events(call_sid, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_twilio_call_events_event_type
    ON twilio_call_events(event_type);
