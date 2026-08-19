-- 0010_add_onboarding_to_users.sql
-- Add onboarding tracking to users table

/* Add onboarding completion tracking */
ALTER TABLE users
ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN DEFAULT false;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMP;

/* Index for efficient onboarding status queries */
CREATE INDEX IF NOT EXISTS idx_users_onboarding_completed ON users(onboarding_completed);
