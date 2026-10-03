"""Offline tests (no network, no DB):  python -m tests.test_parsers"""
from civic.loaders.congress_bills import bill_row, ordinal
from civic.loaders.federal_votes import congress_years, parse_house, parse_senate
from civic.loaders.state_bills import bill_rows
from civic.loaders.state_people import person_row
from civic.util import CD_AT_LARGE, norm_district, norm_option, party_code

HOUSE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote><vote-metadata><congress>119</congress><session>1st</session>
<rollcall-num>42</rollcall-num><legis-num>H R 23</legis-num><vote-question>On Passage</vote-question>
<vote-result>Passed</vote-result><action-date>9-Jan-2025</action-date></vote-metadata>
<vote-data>
<recorded-vote><legislator name-id="A000055" party="R" state="AL">Aderholt</legislator><vote>Yea</vote></recorded-vote>
<recorded-vote><legislator name-id="B001300" party="D" state="CA">Barragan</legislator><vote>Not Voting</vote></recorded-vote>
<recorded-vote><legislator name-id="C000001" party="D" state="IL">Casten</legislator><vote>No</vote></recorded-vote>
</vote-data></rollcall-vote>"""

SENATE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<roll_call_vote><congress>119</congress><session>1</session><vote_number>7</vote_number>
<vote_date>January 9, 2025,  02:24 PM</vote_date>
<vote_question_text>On the Motion to Proceed S. 5</vote_question_text>
<vote_result_text>Motion to Proceed Agreed to (84-9)</vote_result_text>
<document><document_name>S. 5</document_name></document>
<members>
<member><member_full>Durbin (D-IL)</member_full><vote_cast>Nay</vote_cast><lis_member_id>S253</lis_member_id></member>
<member><member_full>Former (R-XX)</member_full><vote_cast>Yea</vote_cast><lis_member_id>S999</lis_member_id></member>
</members></roll_call_vote>"""


def test_util():
    assert norm_district("007") == "7"
    assert norm_district("00", CD_AT_LARGE) == "AL"
    assert norm_district("98", CD_AT_LARGE) == "AL"
    assert norm_district("98") == "98"                 # PA House district 98 stays 98
    assert norm_district("Chittenden-1") == "CHITTENDEN-1"
    assert party_code("Democratic-Farmer-Labor") == "D" and party_code("Republican") == "R"
    assert norm_option("Aye") == "yes" and norm_option("Nay") == "no"
    assert norm_option("Not Voting") == "not_voting" and norm_option("excused") == "not_voting"
    assert norm_option("Jeffries") == "other"          # Speaker election votes are names
    assert congress_years(119) == [(1, 2025), (2, 2026)]
    assert ordinal(119) == "119th" and ordinal(111) == "111th" and ordinal(102) == "102nd"


def test_house():
    ev, casts = parse_house(HOUSE_XML)
    assert ev["vote_id"] == "us-house-119-1-42"
    assert str(ev["vote_date"]) == "2025-01-09"
    assert (ev["yes_count"], ev["no_count"], ev["other_count"]) == (1, 1, 1)
    assert casts[1]["option"] == "not_voting" and casts[1]["politician_id"] == "B001300"


def test_senate():
    ev, casts = parse_senate(SENATE_XML, {"S253": "D000563"})
    assert ev["vote_id"] == "us-senate-119-1-7" and str(ev["vote_date"]) == "2025-01-09"
    assert ev["bill_ref"] == "S. 5"
    assert casts[0]["politician_id"] == "D000563" and casts[0]["option"] == "no"
    assert casts[1]["politician_id"] is None and casts[1]["voter_key"] == "lis:S999"


def test_congress_bill():
    row = bill_row({"congress": 119, "type": "HR", "number": "1234", "title": "A bill",
                    "introducedDate": "2025-02-01",
                    "latestAction": {"actionDate": "2025-07-04", "text": "Became Public Law No: 119-21."}})
    assert row["bill_id"] == "us-119-hr-1234" and row["became_law"] and row["chamber"] == "lower"
    assert row["url"].endswith("/119th-congress/house-bill/1234")
    assert bill_row({"congress": 119, "amendmentNumber": "5"}) is None


def test_state_person():
    doc = {"id": "ocd-person/abc", "name": "Jane Doe", "given_name": "Jane", "family_name": "Doe",
           "party": [{"name": "Democratic"}],
           "roles": [{"type": "lower", "district": "43", "start_date": "2021-01-13", "end_date": "2023-01-11"},
                     {"type": "upper", "district": "22", "start_date": "2023-01-11"}],
           "links": [{"url": "https://ilga.gov/x"}]}
    row = person_row(doc, "IL", today="2026-10-03")
    assert row["chamber"] == "upper" and row["district"] == "22" and row["party_code"] == "D"
    gone = dict(doc, roles=[{"type": "lower", "district": "1", "end_date": "2024-01-01"}])
    assert person_row(gone, "IL", today="2026-10-03") is None
    ne = dict(doc, roles=[{"type": "legislature", "district": "5"}])
    assert person_row(ne, "NE", today="2026-10-03")["chamber"] == "upper"


def test_state_bill():
    b = {"id": "ocd-bill/1", "identifier": "SB 100", "title": "T", "session": "104th",
         "from_organization": {"classification": "upper"}, "first_action_date": "2025-01-15",
         "latest_action_date": "2025-08-01T00:00:00", "latest_action_description": "Public Act",
         "actions": [{"classification": ["executive-signature", "became-law"]}],
         "sponsorships": [{"name": "Doe", "primary": True, "person": {"id": "ocd-person/abc"}},
                          {"name": "Unknown", "primary": False, "person": None}],
         "votes": [{"id": "ocd-vote/1", "motion_text": "Third Reading", "result": "pass",
                    "start_date": "2025-04-10", "organization": {"classification": "upper"},
                    "counts": [{"option": "yes", "value": 40}, {"option": "no", "value": 15},
                               {"option": "not voting", "value": 4}],
                    "votes": [{"option": "yes", "voter_name": "Doe", "voter": {"id": "ocd-person/abc"}},
                              {"option": "excused", "voter_name": "Roe", "voter": None}]}]}
    bill, spons, events, casts = bill_rows("IL", b)
    assert bill["became_law"] and bill["latest_action_date"] == "2025-08-01"
    assert spons[1]["sponsor_key"] == "name:Unknown"
    assert (events[0]["yes_count"], events[0]["no_count"], events[0]["other_count"]) == (40, 15, 4)
    assert casts[1]["option"] == "not_voting" and casts[1]["voter_key"] == "name:Roe"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} tests passed")
