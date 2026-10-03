-- Civic report card schema (PostgreSQL 14+ with PostGIS). Idempotent: safe to re-run.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS civic;

-- One row per officeholder. politician_id = bioguide ID (federal) or ocd-person ID (state).
CREATE TABLE IF NOT EXISTS civic.politician (
    politician_id text PRIMARY KEY,
    level         text NOT NULL CHECK (level IN ('federal', 'state')),
    chamber       text NOT NULL CHECK (chamber IN ('upper', 'lower')),
    state         char(2) NOT NULL,
    district      text,                 -- normalized ('7', 'AL', named); NULL for US senators
    full_name     text NOT NULL,
    first_name    text,
    last_name     text,
    party         text,
    party_code    char(1),              -- D / R / I / O
    is_current    boolean NOT NULL DEFAULT true,
    term_start    date,
    term_end      date,
    bioguide_id   text,
    lis_id        text,                 -- Senate roll-call member id
    icpsr_id      text,                 -- Voteview
    govtrack_id   text,
    fec_ids       text[] NOT NULL DEFAULT '{}',
    openstates_id text,
    url           text,
    photo_url     text,
    email         text,
    extra         jsonb,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS politician_seat_idx
    ON civic.politician (level, chamber, state, district) WHERE is_current;
CREATE INDEX IF NOT EXISTS politician_lis_idx ON civic.politician (lis_id) WHERE lis_id IS NOT NULL;

-- Census TIGER/Line district polygons (NAD83 / EPSG:4269).
CREATE TABLE IF NOT EXISTS civic.district_boundary (
    boundary_id serial PRIMARY KEY,
    level       text NOT NULL,          -- federal (congressional) | state
    chamber     text NOT NULL,          -- upper | lower
    state       char(2) NOT NULL,
    state_fips  char(2) NOT NULL,
    district    text NOT NULL,
    geoid       text NOT NULL,
    name        text,
    vintage     int NOT NULL,           -- TIGER year
    geom        geometry(MultiPolygon, 4269) NOT NULL,
    UNIQUE (level, chamber, geoid, vintage)
);
CREATE INDEX IF NOT EXISTS district_boundary_geom_idx ON civic.district_boundary USING gist (geom);

CREATE TABLE IF NOT EXISTS civic.bill (
    bill_id            text PRIMARY KEY,   -- 'us-119-hr-1234' or ocd-bill/...
    level              text NOT NULL,
    state              char(2),
    session            text,
    chamber            text,
    identifier         text,
    title              text,
    introduced_date    date,
    latest_action_date date,
    latest_action      text,
    became_law         boolean NOT NULL DEFAULT false,
    policy_area        text,
    url                text,
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bill_state_session_idx ON civic.bill (level, state, session);

CREATE TABLE IF NOT EXISTS civic.sponsorship (
    bill_id       text NOT NULL REFERENCES civic.bill (bill_id) ON DELETE CASCADE,
    sponsor_key   text NOT NULL,        -- politician_id when known, else 'name:<name>'
    politician_id text,
    sponsor_name  text,
    is_primary    boolean NOT NULL,
    PRIMARY KEY (bill_id, sponsor_key)
);
CREATE INDEX IF NOT EXISTS sponsorship_politician_idx ON civic.sponsorship (politician_id);

CREATE TABLE IF NOT EXISTS civic.vote_event (
    vote_id     text PRIMARY KEY,       -- 'us-house-119-1-42', 'us-senate-119-1-7', ocd-vote/...
    level       text NOT NULL,
    state       char(2),
    chamber     text,                   -- upper | lower | committee (state)
    session     text,
    vote_date   date,
    question    text,
    result      text,
    bill_ref    text,
    yes_count   int,
    no_count    int,
    other_count int,
    source_url  text
);
CREATE INDEX IF NOT EXISTS vote_event_date_idx ON civic.vote_event (level, chamber, vote_date);

CREATE TABLE IF NOT EXISTS civic.vote_cast (
    vote_id       text NOT NULL REFERENCES civic.vote_event (vote_id) ON DELETE CASCADE,
    voter_key     text NOT NULL,        -- politician_id when known, else lis:/name: key
    politician_id text,
    voter_name    text,
    option        text NOT NULL,        -- yes | no | present | not_voting | other
    raw_option    text,
    PRIMARY KEY (vote_id, voter_key)
);
CREATE INDEX IF NOT EXISTS vote_cast_politician_idx ON civic.vote_cast (politician_id);

CREATE TABLE IF NOT EXISTS civic.finance_summary (
    politician_id            text NOT NULL,
    fec_candidate_id         text NOT NULL,
    cycle                    int  NOT NULL,
    receipts                 numeric,
    disbursements            numeric,
    individual_contributions numeric,
    pac_contributions        numeric,
    cash_on_hand             numeric,
    coverage_end_date        date,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (politician_id, fec_candidate_id, cycle)
);

CREATE TABLE IF NOT EXISTS civic.ideology (
    politician_id text NOT NULL,         -- bioguide
    congress      int  NOT NULL,
    chamber       text NOT NULL,
    icpsr_id      text,
    nominate_dim1 double precision,      -- liberal (-1) .. conservative (+1)
    nominate_dim2 double precision,
    PRIMARY KEY (politician_id, congress, chamber)
);

CREATE TABLE IF NOT EXISTS civic.etl_state (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS civic.etl_run (
    run_id      serial PRIMARY KEY,
    job         text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running',
    rows        int,
    message     text
);
