"""Publication of platform data to the warehouse (``plataforma`` schema of the DWH).

Any module that wants its records in the DWH writes an event to ``dwh_outbox`` in the same
transaction as the change; a background worker sends the events with retries over a
dedicated, write-limited connection. The platform never waits for the DWH: if the
warehouse is down, writes go on and the events are sent when it comes back.
"""
