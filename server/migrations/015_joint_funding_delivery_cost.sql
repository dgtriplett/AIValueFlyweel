-- Add delivery_cost field to funding_requests for user-entered labor cost
-- (replaces the auto-calculated delivery_cost_mid with an editable value)

ALTER TABLE funding_requests ADD COLUMN IF NOT EXISTS delivery_cost NUMERIC;

COMMENT ON COLUMN funding_requests.delivery_cost IS 'User-entered labor/people cost to deliver the enabled use cases (optional override of the auto-suggested estimate)';
