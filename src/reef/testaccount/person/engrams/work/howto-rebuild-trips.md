+++
title = "Rebuilding trips"
tags = ["work", "howto"]
+++

# Rebuilding trips

1. Pause the Dagster schedule `trips_hourly`.
2. Delete the rows in `trips` from the date you need: `DELETE FROM trips WHERE started_at >= '2026-11-01';`
3. Start the job `rebuild_trips` with the partition for that date.
4. Check the trip count with the Metabase card "Trips per day". Expect about 3,100 per day.
5. Switch the schedule back on.

The 20-minute threshold lives in `config/trips.toml` ([[work/decisions-2026.md]]).
