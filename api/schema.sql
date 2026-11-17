-- PetalBoard schema. Applied once to an empty database by db.migrate().
-- Money is whole rupees (bigint). Weights are kg with up to 3 decimals (0.250 = 250 g).

BEGIN;

CREATE TABLE schema_meta (version int NOT NULL);
INSERT INTO schema_meta VALUES (1);

-- People who log in.
CREATE TABLE users (
    id            serial PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    display_name  text NOT NULL,
    role          text NOT NULL CHECK (role IN ('owner', 'manager', 'clerk', 'viewer')),
    password_hash text,
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    token_hash text PRIMARY KEY,
    user_id    int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_seen  timestamptz
);

-- What is printed at the top of every bill. One row only.
CREATE TABLE shop_settings (
    id                     int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    name_ur                text NOT NULL,
    name_en                text NOT NULL,
    tagline_ur             text NOT NULL,
    address_ur             text NOT NULL,
    proprietor_label_ur    text NOT NULL,
    proprietor1_name_ur    text NOT NULL,
    proprietor1_phone      text NOT NULL,
    proprietor2_name_ur    text NOT NULL,
    proprietor2_phone      text NOT NULL,
    default_commission_pct numeric(5,2) NOT NULL DEFAULT 10
);
INSERT INTO shop_settings VALUES (
    1,
    'توقیر احمد فلاور ہاؤس',
    'Tauqeer Ahmed Flower House',
    'ہمارے یہاں نوٹوں کے ہار، نوٹوں کے بوکیٹ، کٹ فلور کے بوکیٹ، روم ڈیکوریشن، کار ڈیکوریشن، گجرے، سہرا بھی دستیاب ہے',
    'ایڈریس : پاکستان چوک حیدرآباد',
    'پروپرائیٹر',
    'واثق ابڑو', '0310-1103312',
    'توقیر چانڈیو', '0315-0313127',
    10
);

-- Kinds of flower bought (Gulab, Motia, ...).
CREATE TABLE flowers (
    id      serial PRIMARY KEY,
    name    text NOT NULL UNIQUE,
    name_ur text,
    active  boolean NOT NULL DEFAULT true
);

-- Suppliers (the people who bring flowers and get a monthly bill).
CREATE TABLE suppliers (
    id             serial PRIMARY KEY,
    name           text NOT NULL UNIQUE,
    name_ur        text,
    phone          text,
    place          text,
    flower_id      int REFERENCES flowers(id),
    commission_pct numeric(5,2) NOT NULL DEFAULT 10 CHECK (commission_pct >= 0 AND commission_pct <= 100),
    notes          text,
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- One bill per supplier per month: the paper sheet.
--   total      = sum of the day amounts
--   commission = floor(total * commission_pct / 100)
--   net        = total - commission                 (رقم)
--   kharcha    = sum of kharcha items               (خرچہ)
--   final      = net - kharcha - opening_due        (ٹوٹل)
-- final > 0: the shop owes the supplier. final < 0: the supplier owes the shop.
-- opening_due is what the supplier still owed from earlier bills; on paper it is
-- written into the day-1 kharcha cell as "2530+200".
CREATE TABLE bills (
    id             serial PRIMARY KEY,
    supplier_id    int NOT NULL REFERENCES suppliers(id),
    month          date NOT NULL CHECK (extract(day FROM month) = 1),
    flower_id      int REFERENCES flowers(id),
    commission_pct numeric(5,2) NOT NULL CHECK (commission_pct >= 0 AND commission_pct <= 100),
    opening_due    bigint NOT NULL DEFAULT 0,
    note           text,
    -- Totals written on an imported paper bill, kept to show where the paper differs.
    paper_totals   jsonb,
    status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    -- Settlement when the bill is closed. carried = final - paid_cash - advance_cut
    -- goes into the next month's opening_due (with the sign flipped).
    paid_cash      bigint,
    advance_cut    bigint,
    carried        bigint,
    closed_at      timestamptz,
    closed_by      int REFERENCES users(id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, month)
);
CREATE INDEX bills_month ON bills(month);

-- One row per day on the bill (وزن، ریٹ، ٹوٹل رقم).
CREATE TABLE bill_days (
    bill_id    int NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    day        int NOT NULL CHECK (day BETWEEN 1 AND 31),
    weight     numeric(10,3),
    rate       numeric(10,2),
    amount     bigint NOT NULL DEFAULT 0,
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by int REFERENCES users(id),
    PRIMARY KEY (bill_id, day)
);

-- Money given to the supplier during the month (خرچہ). Several per day are allowed;
-- the bill shows them joined with "+", like "1250+200".
CREATE TABLE kharcha (
    id         serial PRIMARY KEY,
    bill_id    int NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    day        int NOT NULL CHECK (day BETWEEN 1 AND 31),
    amount     bigint NOT NULL,
    tag        text,          -- the small letter written beside the amount, e.g. "B"
    note       text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by int REFERENCES users(id)
);
CREATE INDEX kharcha_bill ON kharcha(bill_id, day);

-- Advance (ایڈوانس) given to a supplier, separate from the monthly bill.
-- amount > 0: advance given. amount < 0: recovered (cash back or cut from a bill).
CREATE TABLE advances (
    id          serial PRIMARY KEY,
    supplier_id int NOT NULL REFERENCES suppliers(id),
    on_date     date NOT NULL,
    amount      bigint NOT NULL CHECK (amount <> 0),
    kind        text NOT NULL CHECK (kind IN ('given', 'recovered', 'bill_cut', 'opening')),
    bill_id     int REFERENCES bills(id) ON DELETE CASCADE,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  int REFERENCES users(id)
);
CREATE INDEX advances_supplier ON advances(supplier_id, on_date);

-- Investors. An investor puts money into a supplier's
-- khaata (usually the advance) and gets a share of the commission that khaata earns.
CREATE TABLE investors (
    id         serial PRIMARY KEY,
    name       text NOT NULL UNIQUE,
    phone      text,
    notes      text,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Who shares a khaata's commission, and how much, from which month.
-- to_month NULL = still running. Earned for a month = bill commission x pct / 100.
CREATE TABLE khaata_shares (
    id          serial PRIMARY KEY,
    supplier_id int NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    investor_id int NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
    pct         numeric(5,2) NOT NULL CHECK (pct > 0 AND pct <= 100),
    from_month  date NOT NULL CHECK (extract(day FROM from_month) = 1),
    to_month    date CHECK (to_month IS NULL OR (extract(day FROM to_month) = 1 AND to_month >= from_month)),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX khaata_shares_supplier ON khaata_shares(supplier_id, from_month);
CREATE INDEX khaata_shares_investor ON khaata_shares(investor_id, from_month);

-- Money between the shop and an investor.
--   invested  (+) investor put money in (optionally into one khaata)
--   withdrawn (-) investor took capital back
--   payout    (-) profit (commission share) paid to the investor
CREATE TABLE investor_entries (
    id          serial PRIMARY KEY,
    investor_id int NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
    supplier_id int REFERENCES suppliers(id) ON DELETE SET NULL,
    on_date     date NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('invested', 'withdrawn', 'payout')),
    amount      bigint NOT NULL CHECK (amount > 0),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  int REFERENCES users(id)
);
CREATE INDEX investor_entries_investor ON investor_entries(investor_id, on_date);

COMMIT;
