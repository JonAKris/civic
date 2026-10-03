-- Report-card metrics and address lookup. Re-runnable (CREATE OR REPLACE).

-- Which districts contain a point (lon/lat, WGS84 ~= NAD83 at this scale)?
CREATE OR REPLACE FUNCTION civic.districts_at(p_lon double precision, p_lat double precision)
RETURNS TABLE (level text, chamber text, state char(2), district text, name text)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (b.level, b.chamber) b.level, b.chamber, b.state, b.district, b.name
    FROM civic.district_boundary b
    WHERE ST_Intersects(b.geom, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4269))
    ORDER BY b.level, b.chamber, b.vintage DESC
$$;

-- Every current official representing a point: US House + 2 US senators + state upper + lower.
CREATE OR REPLACE FUNCTION civic.officials_at(p_lon double precision, p_lat double precision)
RETURNS SETOF civic.politician
LANGUAGE sql STABLE AS $$
    WITH d AS (SELECT * FROM civic.districts_at(p_lon, p_lat))
    SELECT p.* FROM civic.politician p
    JOIN d ON p.level = d.level AND p.chamber = d.chamber
          AND p.state = d.state AND p.district = d.district
    WHERE p.is_current
    UNION ALL
    SELECT p.* FROM civic.politician p
    WHERE p.is_current AND p.level = 'federal' AND p.chamber = 'upper'
      AND p.state IN (SELECT d.state FROM d)
$$;

-- Floor-vote attendance (committee votes excluded).
CREATE OR REPLACE VIEW civic.v_vote_participation AS
SELECT vc.politician_id,
       count(*)                                             AS votes_eligible,
       count(*) FILTER (WHERE vc.option = 'not_voting')     AS votes_missed,
       round(100.0 * count(*) FILTER (WHERE vc.option = 'not_voting')
             / nullif(count(*), 0), 1)                      AS missed_pct,
       min(ve.vote_date)                                    AS first_vote,
       max(ve.vote_date)                                    AS last_vote
FROM civic.vote_cast vc
JOIN civic.vote_event ve USING (vote_id)
WHERE vc.politician_id IS NOT NULL
  AND ve.chamber IN ('upper', 'lower')
GROUP BY vc.politician_id;

CREATE OR REPLACE VIEW civic.v_bill_activity AS
SELECT s.politician_id,
       count(*) FILTER (WHERE s.is_primary)                  AS bills_sponsored,
       count(*) FILTER (WHERE s.is_primary AND b.became_law) AS bills_enacted,
       count(*) FILTER (WHERE NOT s.is_primary)              AS bills_cosponsored
FROM civic.sponsorship s
JOIN civic.bill b USING (bill_id)
WHERE s.politician_id IS NOT NULL
GROUP BY s.politician_id;

-- Share of a member's cosponsorships that back a bill whose primary sponsor is from the other major party.
CREATE OR REPLACE VIEW civic.v_bipartisanship AS
WITH co AS (
    SELECT s.politician_id, me.party_code AS my_party, sp_p.party_code AS sponsor_party
    FROM civic.sponsorship s
    JOIN civic.politician me   ON me.politician_id = s.politician_id
    JOIN civic.sponsorship sp  ON sp.bill_id = s.bill_id AND sp.is_primary
                              AND sp.politician_id IS NOT NULL
                              AND sp.politician_id <> s.politician_id
    JOIN civic.politician sp_p ON sp_p.politician_id = sp.politician_id
    WHERE NOT s.is_primary
)
SELECT politician_id,
       count(*)                                             AS cosponsorships_scored,
       count(*) FILTER (WHERE sponsor_party <> my_party)    AS cross_party,
       round(100.0 * count(*) FILTER (WHERE sponsor_party <> my_party)
             / nullif(count(*), 0), 1)                      AS cross_party_pct
FROM co
WHERE my_party IN ('D', 'R') AND sponsor_party IN ('D', 'R')
GROUP BY politician_id;

CREATE OR REPLACE VIEW civic.v_report_card AS
SELECT p.politician_id, p.level, p.chamber, p.state, p.district,
       p.full_name, p.party, p.party_code, p.url, p.photo_url,
       vp.votes_eligible, vp.votes_missed, vp.missed_pct,
       ba.bills_sponsored, ba.bills_enacted, ba.bills_cosponsored,
       bp.cosponsorships_scored, bp.cross_party_pct,
       fin.cycle AS finance_cycle, fin.receipts, fin.individual_contributions,
       fin.pac_contributions, fin.cash_on_hand,
       ide.congress AS ideology_congress, ide.nominate_dim1, ide.nominate_dim2
FROM civic.politician p
LEFT JOIN civic.v_vote_participation vp ON vp.politician_id = p.politician_id
LEFT JOIN civic.v_bill_activity      ba ON ba.politician_id = p.politician_id
LEFT JOIN civic.v_bipartisanship     bp ON bp.politician_id = p.politician_id
LEFT JOIN LATERAL (
    SELECT f.cycle,
           sum(f.receipts)                 AS receipts,
           sum(f.individual_contributions) AS individual_contributions,
           sum(f.pac_contributions)        AS pac_contributions,
           sum(f.cash_on_hand)             AS cash_on_hand
    FROM civic.finance_summary f
    WHERE f.politician_id = p.politician_id
    GROUP BY f.cycle
    ORDER BY f.cycle DESC
    LIMIT 1
) fin ON true
LEFT JOIN LATERAL (
    SELECT i.congress, i.nominate_dim1, i.nominate_dim2
    FROM civic.ideology i
    WHERE i.politician_id = p.politician_id
    ORDER BY i.congress DESC
    LIMIT 1
) ide ON true
WHERE p.is_current;
