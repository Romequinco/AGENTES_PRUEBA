-- Sprint 2: Portfolio Tracker Global
-- Extiende portfolio_positions para soportar multi-asset y ownership directo por usuario.

ALTER TABLE portfolio_positions
  ALTER COLUMN portfolio_id DROP NOT NULL;

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS asset_type VARCHAR(20) NOT NULL DEFAULT 'stock',
  ADD COLUMN IF NOT EXISTS exchange    VARCHAR(50),
  ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_portfolio_positions_user_id
  ON portfolio_positions(user_id);
