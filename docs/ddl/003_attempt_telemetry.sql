-- Sidecar leaves frozen attempt rows and old binary INSERT arity intact.
CREATE TABLE fetch_attempt_telemetry (
 attempt_id INTEGER PRIMARY KEY REFERENCES fetch_attempts(id),
 elapsed_seconds REAL NOT NULL CHECK(elapsed_seconds >= 0),
 wait_seconds REAL NOT NULL CHECK(wait_seconds >= 0)
);
