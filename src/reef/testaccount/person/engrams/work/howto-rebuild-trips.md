+++
title = "Ritten opnieuw opbouwen"
tags = ["work", "howto"]
+++

# Ritten opnieuw opbouwen

1. Zet de Dagster-schedule `trips_hourly` op pauze.
2. Verwijder de rijen in `trips` vanaf de gewenste datum: `DELETE FROM trips WHERE started_at >= '2026-11-01';`
3. Start de job `rebuild_trips` met de partitie van de datum.
4. Controleer het aantal ritten met de query in Metabase, kaart "Ritten per dag". Verwacht ongeveer 3.100 per dag.
5. Zet de schedule weer aan.

De drempel van 20 minuten staat in `config/trips.toml` ([[work/decisions-2026.md]]).
