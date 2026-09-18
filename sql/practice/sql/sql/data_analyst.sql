  1. Find the top 10 grounds by number of matches
  SELECT
    venue AS ground,
    COUNT(DISTINCT match_id) AS total_matches
FROM matches
GROUP BY venue
ORDER BY total_matches DESC
LIMIT 10;
Explanation
COUNT(DISTINCT match_id) counts each match only once.
GROUP BY venue groups matches by ground.
ORDER BY ... DESC puts the grounds with the most matches first.
LIMIT 10 returns only the top 10

2. Grounds with average innings score above 165, minimum 25 matches
SELECT
    venue AS ground,
    COUNT(DISTINCT match_id) AS total_matches,
    AVG(innings_score) AS avg_innings_score
FROM innings
GROUP BY venue
HAVING COUNT(DISTINCT match_id) >= 25
   AND AVG(innings_score) > 165
ORDER BY avg_innings_score DESC;
Explanation
AVG(innings_score) calculates the average innings score.
COUNT(DISTINCT match_id) >= 25 ensures that the ground has at least 25 matches.
HAVING is used because we are filtering grouped/aggregated results.
Only grounds with an average score greater than 165 are shown.

If your dataset stores innings score in deliveries, you may first need to calculate the total score for each innings.
3. Calculate chase win percentage for grounds with at least 50 matches
SELECT
    venue AS ground,
    COUNT(DISTINCT match_id) AS total_matches,
    SUM(CASE
            WHEN win_type = 'chase'
            THEN 1
            ELSE 0
        END) * 100.0 / COUNT(DISTINCT match_id) AS chase_win_percentage
FROM matches
GROUP BY venue
HAVING COUNT(DISTINCT match_id) >= 50
ORDER BY chase_win_percentage DESC;
Explanation

The formula is:

Chase Win % = Chase Wins / Total Matches × 100
CASE counts matches won while chasing.
SUM() adds those chase wins.
COUNT(DISTINCT match_id) gives the total number of matches.
HAVING >= 50 keeps only grounds with at least 50 matches.
100.0 ensures the result is calculated as a decimal percentage.
4. Count the number of unique cleaned venues
SELECT
    COUNT(DISTINCT cleaned_venue) AS unique_cleaned_venues
FROM matches;
Explanation
DISTINCT removes duplicate venue names.
COUNT() counts the remaining unique names.
cleaned_venue should contain standardized venue names.
5. Find the five grounds with the lowest powerplay run rate

Assuming the powerplay is the first 6 overs:

SELECT
    venue AS ground,
    SUM(runs) * 6.0 / COUNT(DISTINCT innings_id) AS powerplay_run_rate
FROM deliveries
WHERE over_number BETWEEN 1 AND 6
GROUP BY venue
ORDER BY powerplay_run_rate ASC
LIMIT 5;
Explanation

Powerplay run rate is:

Powerplay Run Rate =
Powerplay Runs / Powerplay Overs
WHERE over_number BETWEEN 1 AND 6 selects the powerplay overs.
SUM(runs) calculates powerplay runs.
GROUP BY venue calculates the value for each ground.
ORDER BY ... ASC puts the lowest run rates first.
LIMIT 5 gives the five lowest grounds.

Important: If your dataset stores overs as 0–5 instead of 1–6, use:

WHERE over_number BETWEEN 0 AND 5
6. Why can COUNT(DISTINCT match_id) be safer than COUNT(*) after a JOIN?
Example

Suppose one match has 120 delivery records.

After joining matches with deliveries:

match_id
--------
101
101
101
101
...

The same match_id appears many times.

If you use:

COUNT(*)

you might count 120 rows instead of 1 match.

But:

COUNT(DISTINCT match_id)

counts:

101 → 1 match
Simple explanation
COUNT(*)

counts rows.

COUNT(DISTINCT match_id)

counts unique matches.

So after a JOIN, where one match can produce multiple rows, COUNT(DISTINCT match_id) is safer when the question is asking for the number of matches.
7. Why day/night cannot be answered from match_date alone

match_date only tells us which date the match happened.

For example:

2026-04-10

doesn't tell us whether the match started in the morning, afternoon, or evening.

To determine day/night, you need additional information such as:

match_start_time

or a field such as:

day_night

For example:

SELECT
    match_date,
    match_start_time,
    day_night
FROM matches;

Explanation:
Two matches can happen on the same date but have different start times:

match_date	start_time	Type
2026-04-10	15:30	Day
2026-04-10	19:30	Night