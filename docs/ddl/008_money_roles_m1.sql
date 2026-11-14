-- 008 money_roles_m1: parser v5 (M1 lane) writes per-item KQLCNT prices into
-- money_amounts. The v1 role CHECK only allowed the RC3-10 roles (see 007).
-- M1 needs ITEM_WINNING_PRICE (the per-item winning unit price:
-- lotResultItems[].formValue[].lotWiningPrice, or goodsList items' bidPrice)
-- and ITEM_BID_PRICE (the item's submitted bid unit price, written only when
-- it differs from the winning price). SQLite cannot ALTER a CHECK, so the
-- table is rebuilt exactly as in 007. Existing rows are copied verbatim; the
-- UNIQUE grain (owner_kind, owner_id, role, currency_code, tax_basis) is
-- unchanged. v1's file is NOT edited: its checksum is recorded in production
-- stores.
CREATE TABLE money_amounts_new (
  id                 INTEGER PRIMARY KEY,
  owner_kind         TEXT NOT NULL CHECK (owner_kind IN ('PACKAGE','LOT','GOODS_ROW','CONTRACT','OFFER')),
  owner_id           INTEGER NOT NULL,
  role               TEXT NOT NULL CHECK (role IN ('PACKAGE_VALUE','BID_PRICE','WIN_PRICE','UNIT_PRICE','CONTRACT_VALUE','BUDGET','ADJUSTMENT','REOFFER_PRICE','REOFFER_PRICE_FINAL','BID_FINAL_PRICE','PACKAGE_PRICE','PACKAGE_PRICE_INIT','PACKAGE_PRICE_STEP','LOT_PRICE','LOT_ESTIMATE_PRICE','LOT_PLAN_PRICE','ITEM_WINNING_PRICE','ITEM_BID_PRICE')),
  presence_state     TEXT NOT NULL CHECK (presence_state IN ('KNOWN','MISSING','UNPARSABLE')),
  value              NUMERIC,
  value_raw          TEXT,
  currency           TEXT NOT NULL CHECK (currency IN ('KNOWN','MISSING','UNPARSABLE')),
  currency_code      TEXT CHECK (currency_code IS NULL OR length(currency_code) = 3),
  tax_basis          TEXT NOT NULL CHECK (tax_basis IN ('INCLUDING_TAX','EXCLUDING_TAX','UNKNOWN','NOT_APPLICABLE')),
  scale_exp          INTEGER CHECK (scale_exp IS NULL OR scale_exp BETWEEN -6 AND 12),
  precision_digits   INTEGER CHECK (precision_digits IS NULL OR precision_digits >= 0),
  provenance_json    TEXT NOT NULL CHECK (provenance_json GLOB '{*'),
  observed_at_utc    TEXT NOT NULL CHECK (observed_at_utc GLOB '[0-9][0-9][0-9][0-9]-*Z'),
  UNIQUE (owner_kind, owner_id, role, currency_code, tax_basis),
  CHECK (
    (presence_state = 'KNOWN' AND value IS NOT NULL AND currency = 'KNOWN' AND currency_code IS NOT NULL)
    OR (presence_state IN ('MISSING','UNPARSABLE') AND value IS NULL)
  ),
  CHECK (presence_state <> 'MISSING' OR value_raw IS NULL)
);
INSERT INTO money_amounts_new (id, owner_kind, owner_id, role, presence_state, value, value_raw, currency, currency_code, tax_basis, scale_exp, precision_digits, provenance_json, observed_at_utc)
  SELECT id, owner_kind, owner_id, role, presence_state, value, value_raw, currency, currency_code, tax_basis, scale_exp, precision_digits, provenance_json, observed_at_utc FROM money_amounts;
DROP TABLE money_amounts;
ALTER TABLE money_amounts_new RENAME TO money_amounts;
CREATE INDEX ix_money_owner ON money_amounts(owner_kind, owner_id);
