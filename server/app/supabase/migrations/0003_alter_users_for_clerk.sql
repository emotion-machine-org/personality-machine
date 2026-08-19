-- 0003_alter_users_for_clerk.sql
/* ------------------------------------------------------------------------
   Adapt users table for Clerk authentication.
   Assumes 0002_init_schema.sql already created:
     users(id UUID PRIMARY KEY, email TEXT UNIQUE, display_name TEXT, created_at TIMESTAMPTZ)
---------------------------------------------------------------------------*/

-- 1. New columns ----------------------------------------------------------
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS clerk_user_id TEXT,                -- Clerk’s user.id
    ADD COLUMN IF NOT EXISTS username       TEXT,
    ADD COLUMN IF NOT EXISTS auth_provider  TEXT,               -- 'google'|'apple'|'email'…
    ADD COLUMN IF NOT EXISTS avatar_url     TEXT,
    ADD COLUMN IF NOT EXISTS updated_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

-- 2. Make sure every row gets an updated_at bump on UPDATE ---------------
DO $$
BEGIN
    -- create the trigger function only once
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc WHERE proname = 'update_updated_at_column'
    ) THEN
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $func$
        BEGIN
            NEW.updated_at := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $func$ LANGUAGE plpgsql;
    END IF;
END $$;

-- attach trigger (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE  tgname = 'trg_users_set_updated_at'
    ) THEN
        CREATE TRIGGER trg_users_set_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- 3. Indexes & uniqueness -------------------------------------------------
-- Unique clerk_user_id, but allow NULLs during back-fill
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_clerk_user_id_unique
    ON users(clerk_user_id)
    WHERE clerk_user_id IS NOT NULL;

-- Optional quick-lookup indexes
CREATE INDEX IF NOT EXISTS idx_users_email          ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_username       ON users(username);

-- 4. (Optional) back-fill existing rows with Clerk IDs -------------------
-- -- After you sync existing accounts, drop NULLs constraint:
-- UPDATE users SET clerk_user_id = '<imported-id>' WHERE id = '<uuid>';
-- ALTER TABLE users ALTER COLUMN clerk_user_id SET NOT NULL;
