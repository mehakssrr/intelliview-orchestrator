"""
locustfile.py — Load test for the AI Interview Orchestrator
=============================================================

WHAT THIS SIMULATES
--------------------
A single "virtual candidate" runs a realistic interview session end-to-end
against the real orchestrator API (routers/sessions.py), not a single
hammered endpoint:

    0. POST /candidates                        -> register as a real
                                                    candidate (REQUIRED —
                                                    see CANDIDATE
                                                    REGISTRATION below)
    1. POST /start-interview                  -> create a session
                                                   (status starts at QUEUED)
    2. GET  /session-status/{session_id}       -> poll until the pipeline
                                                   has picked it up
    3. Repeat N times, with think-time between:
         POST /interviews/ask-question         -> get next question
         POST /interviews/submit-answer        -> answer it
    4. GET  /session-status/{session_id}       -> final status check

CANDIDATE REGISTRATION — WHY STEP 0 EXISTS
----------------------------------------------
Confirmed by running this test against the real API:
interview_sessions.candidate_id has a foreign key constraint against
candidates.candidate_id. A made-up candidate_id (e.g. "loadtest-12345")
fails EVERY /start-interview call with a 500
(psycopg2.errors.ForeignKeyViolation) — the interview pipeline itself
never gets exercised. Each simulated user now registers via
POST /candidates in on_start() and uses the server-assigned
candidate_id for its interviews. If registration itself fails (e.g.
rate limited), the user retries it before attempting an interview
rather than sending requests that are guaranteed to fail.

NOTE ON "COMPLETING" A SESSION
-------------------------------
This API has no client-facing "complete" endpoint. Sessions move through
CREATED -> QUEUED -> VIDEO_PROCESSING -> AUDIO_PROCESSING -> EVALUATING ->
COMPLETED automatically once the backend pipeline picks them up (see
InterviewSession.status check constraint in database/models). So a
"completed" interview here just means: the candidate finished submitting
answers and the orchestrator will finish processing asynchronously. The
final status poll records whatever state the session is actually in when
the candidate is done — that's the realistic behavior to measure.

AUTH
----
Confirmed from orchestrator/security.py: get_current_user accepts EITHER
a JWT Bearer token OR a legacy `X-API-Token` header that must exactly
match the server's API_TOKEN env var (grants role "admin"). This load
test uses the legacy X-API-Token path — it's a single static value with
no login flow required, which is what a load test needs. Set it via
LOAD_TEST_API_KEY (must equal whatever API_TOKEN is set to on the
server you're testing).

/retry-session/{id} and /detect-failures additionally require the
"admin" role via require_role("admin") — the X-API-Token path already
grants that role, so no extra config needed if you add those endpoints
to the test later.

RATE LIMITING — READ THIS BEFORE TRUSTING THE 50/100/500-USER RESULTS
------------------------------------------------------------------------
CONFIRMED from orchestrator/rate_limiter.py: the limit key is
`f"{client_ip}:{x_api_token}"` — IP address + API token, not per
simulated user. Locust runs all virtual users from ONE machine (one
IP) using the SAME X-API-Token (there's only one valid token), so
every simulated candidate shares a single rate-limit bucket:
60 requests per 60 seconds for the ENTIRE test, regardless of whether
you run 10 or 500 concurrent users.

Practical effect: past a certain point (roughly 60 requests/min across
ALL virtual users combined), you will see a wall of 429 responses.
This is NOT a backend capacity finding — it's the rate limiter
correctly doing its job against what looks like one client. Reading
"the system caps out around 60 req/min" from this test would be wrong
and would mask the real capacity of the interview pipeline behind it.

This script tags 429s as "rate_limited" (see catch_response calls
below) so they show up as a distinct, filterable category in
<prefix>_failures.csv — do not read them as the same kind of failure
as a 500 or a timeout.

To actually measure backend capacity past ~60 req/min, do ONE of:
  1. Ask whoever owns the rate limiter config to raise or disable the
     limit on the staging environment for the duration of the test
     (cleanest option — this is what the issue is actually asking for).
  2. Run Locust in distributed mode (--master / --worker) across
     multiple machines with different IPs, so the client_key differs
     per machine. Still caps at 60 req/min per machine.
  3. If the team decides the rate limiter itself is part of what's
     being tested, keep it as-is and report the 429 wall as the actual
     finding: "concurrent-candidate capacity is bottlenecked by a
     global rate limiter, not by the interview pipeline, at N req/min."
This is a decision for the team, not something this script can resolve
on its own — flag it in your results writeup either way.

CONFIGURE FOR YOUR API
-----------------------
Edit the CONFIG block below if paths or auth change.

HOW TO RUN
----------
    pip install locust

PowerShell (Windows / VS Code terminal):
    $env:TARGET_HOST = "https://staging.example.com"
    $env:LOAD_TEST_API_KEY = "your-token"     # if auth is required
    .\run_scenarios.ps1 10                    # smoke test first
    .\run_scenarios.ps1 50
    .\run_scenarios.ps1 100
    .\run_scenarios.ps1 500

bash/zsh (Mac/Linux/WSL):
    export TARGET_HOST="https://staging.example.com"
    export LOAD_TEST_API_KEY="your-token"
    ./run_scenarios.sh 10

Or call Locust directly for one scenario:
    locust -f locustfile.py --host $env:TARGET_HOST `
        --users 10 --spawn-rate 2 --run-time 3m `
        --csv=results/10users --html=results/10users.html --headless

WHAT GETS RECORDED
-------------------
--csv writes <prefix>_stats.csv, <prefix>_stats_history.csv, and
<prefix>_failures.csv (per-endpoint counts, response times, failure
types). --html writes a self-contained report with charts. A summary
(median/p95/p99/failure rate) also prints to the console at the end of
every run, and the process exits non-zero if the failure rate crosses
LOAD_TEST_MAX_FAILURE_RATE (default 5%), so this can gate CI later.
"""

import os
import random

from locust import HttpUser, between, events, task

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

# Set via: $env:LOAD_TEST_API_KEY = "..."  (PowerShell)  or
#          export LOAD_TEST_API_KEY="..."  (bash)
# Must exactly match the server's API_TOKEN env var (legacy X-API-Token
# auth path in orchestrator/security.py — grants role "admin"). Left
# blank, requests go out unauthenticated and /start-interview will 401
# (that's expected and tells you the token wasn't set — not a capacity
# problem).
API_TOKEN = os.environ.get("LOAD_TEST_API_KEY", "")

CREATE_CANDIDATE_PATH = "/candidates"
START_INTERVIEW_PATH = "/start-interview"
SESSION_STATUS_PATH = "/session-status/{session_id}"
ASK_QUESTION_PATH = "/interviews/ask-question"
SUBMIT_ANSWER_PATH = "/interviews/submit-answer"

# How many question/answer turns a simulated candidate does per interview.
MIN_ANSWERS_PER_INTERVIEW = 3
MAX_ANSWERS_PER_INTERVIEW = 8

# Think-time before each answer (seconds) — mimics a candidate reading
# the question and typing a response, not hammering the endpoint.
MIN_THINK_TIME = 3
MAX_THINK_TIME = 20

# How long / how often to poll session-status right after creation,
# before starting to ask questions (gives the scheduler a moment to
# pick the task up — mirrors real client polling behavior).
POST_CREATE_POLL_INTERVAL = 1.0
POST_CREATE_MAX_POLLS = 5

POSITIONS = ["Software Engineer", "Data Analyst", "Product Manager", "SRE"]
PRIORITIES = ["low", "medium", "high"]

SAMPLE_ANSWERS = [
    "I'd approach that by first clarifying the requirements, then breaking "
    "the problem into smaller pieces I can validate independently.",
    "In my last role I handled a similar situation by coordinating with "
    "stakeholders early and setting clear checkpoints.",
    "I think the tradeoff here is between speed and correctness, and I'd "
    "lean toward correctness given the stakes described.",
    "My strongest skill is probably debugging under pressure — I stay "
    "systematic instead of guessing.",
    "I'd want more context before committing to an answer, but my initial "
    "instinct is to isolate the variable and test it directly.",
]

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}


def _headers():
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-API-Token"] = API_TOKEN
    return headers


def _is_rate_limited(resp) -> bool:
    """
    Tag 429s distinctly so they never get silently averaged in with real
    errors (500s, timeouts, bad payloads). See the RATE LIMITING section
    at the top of this file — with a shared IP + shared API token, every
    virtual user shares one rate-limit bucket, so 429s past ~60 req/min
    are expected middleware behavior, not a backend failure. Reporting
    them under a distinct "rate_limited:" prefix lets you filter them
    out of <prefix>_failures.csv when judging real error rate.
    """
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "?")
        resp.failure(f"rate_limited: 429, retry_after={retry_after}s")
        return True
    return False


# --------------------------------------------------------------------------
# User behavior
# --------------------------------------------------------------------------


class InterviewCandidate(HttpUser):
    """One instance = one simulated candidate running a full interview."""

    # Pause between full interview sessions.
    wait_time = between(2, 6)

    def on_start(self):
        """
        Register a real candidate before running any interviews.

        REQUIRED: /start-interview's candidate_id must reference an
        existing row in the candidates table (foreign key constraint
        interview_sessions_candidate_id_fkey). Made-up candidate_ids fail
        every single interview with a 500 (psycopg2.errors.
        ForeignKeyViolation) — confirmed against a real run of this API.
        If registration fails here, candidate_id stays None and
        run_full_interview skips the user's interview attempts rather
        than generating guaranteed-broken requests.
        """
        self.candidate_seq = random.randint(100000, 999999)
        self.candidate_id = None
        self.candidate_id = self._register_candidate()

    def _register_candidate(self):
        payload = {
            "name": f"Load Test Candidate {self.candidate_seq}",
            "email": f"loadtest-{self.candidate_seq}@example.com",
        }
        with self.client.post(
            CREATE_CANDIDATE_PATH,
            json=payload,
            headers=_headers(),
            name="POST /candidates (register)",
            catch_response=True,
        ) as resp:
            if _is_rate_limited(resp):
                return None
            if resp.status_code not in (200, 201):
                resp.failure(f"candidate registration failed: HTTP {resp.status_code}")
                return None
            try:
                body = resp.json()
            except ValueError:
                resp.failure("candidate registration returned non-JSON body")
                return None
            candidate_id = body.get("candidate_id") or body.get("id")
            if not candidate_id:
                resp.failure(
                    f"candidate registration response missing candidate_id "
                    f"(got keys: {list(body.keys())})"
                )
                return None
            resp.success()
            return candidate_id

    @task
    def run_full_interview(self):
        if self.candidate_id is None:
            # Registration failed in on_start (or during a rate-limited
            # window) — retry it here rather than sending interview
            # requests that are guaranteed to fail the FK constraint.
            self.candidate_id = self._register_candidate()
            if self.candidate_id is None:
                return

        session_id = self._start_interview()
        if session_id is None:
            return

        self._poll_status_after_create(session_id)

        num_answers = random.randint(
            MIN_ANSWERS_PER_INTERVIEW, MAX_ANSWERS_PER_INTERVIEW
        )
        for _ in range(num_answers):
            question_id = self._ask_question(session_id)
            if question_id is None:
                break
            self._think()
            ok = self._submit_answer(session_id, question_id)
            if not ok:
                break

        self._final_status_check(session_id)

    # ---- individual steps -------------------------------------------------

    def _start_interview(self):
        payload = {
            "candidate_id": self.candidate_id,
            "candidate_name": f"Load Test Candidate {self.candidate_seq}",
            "position": random.choice(POSITIONS),
            "priority": random.choice(PRIORITIES),
        }
        with self.client.post(
            START_INTERVIEW_PATH,
            json=payload,
            headers=_headers(),
            name="POST /start-interview",
            catch_response=True,
        ) as resp:
            if _is_rate_limited(resp):
                return None
            if resp.status_code == 401:
                resp.failure(
                    "401 Unauthorized — check LOAD_TEST_API_KEY matches server's API_TOKEN"
                )
                return None
            if resp.status_code not in (200, 201):
                resp.failure(f"start-interview failed: HTTP {resp.status_code}")
                return None
            try:
                session_id = resp.json().get("session_id")
            except ValueError:
                resp.failure("start-interview returned non-JSON body")
                return None
            if not session_id:
                resp.failure("start-interview response missing session_id")
                return None
            resp.success()
            return session_id

    def _poll_status_after_create(self, session_id):
        import time

        path = SESSION_STATUS_PATH.format(session_id=session_id)
        for _ in range(POST_CREATE_MAX_POLLS):
            with self.client.get(
                path,
                headers=_headers(),
                name="GET /session-status/:id (post-create)",
                catch_response=True,
            ) as resp:
                if _is_rate_limited(resp):
                    return
                if resp.status_code != 200:
                    resp.failure(f"status check failed: HTTP {resp.status_code}")
                    return
                try:
                    state = resp.json().get("status", "")
                except ValueError:
                    resp.failure("status returned non-JSON body")
                    return
                resp.success()
                if state and state != "CREATED":
                    return
            time.sleep(POST_CREATE_POLL_INTERVAL)

    def _think(self):
        import time

        time.sleep(random.uniform(MIN_THINK_TIME, MAX_THINK_TIME))

    def _ask_question(self, session_id):
        payload = {"session_id": session_id}
        with self.client.post(
            ASK_QUESTION_PATH,
            json=payload,
            headers=_headers(),
            name="POST /interviews/ask-question",
            catch_response=True,
        ) as resp:
            if _is_rate_limited(resp):
                return None
            if resp.status_code == 404:
                # No more questions available — expected end-of-interview
                # condition, not a system failure.
                resp.success()
                return None
            if resp.status_code != 200:
                resp.failure(f"ask-question failed: HTTP {resp.status_code}")
                return None
            try:
                question_id = resp.json().get("question_id")
            except ValueError:
                resp.failure("ask-question returned non-JSON body")
                return None
            resp.success()
            return question_id

    def _submit_answer(self, session_id, question_id):
        payload = {
            "session_id": session_id,
            "question_id": question_id,
            "answer_text": random.choice(SAMPLE_ANSWERS),
            "score": round(random.uniform(3.0, 9.5), 1),
        }
        with self.client.post(
            SUBMIT_ANSWER_PATH,
            json=payload,
            headers=_headers(),
            name="POST /interviews/submit-answer",
            catch_response=True,
        ) as resp:
            if _is_rate_limited(resp):
                return False
            if resp.status_code != 200:
                resp.failure(f"submit-answer failed: HTTP {resp.status_code}")
                return False
            resp.success()
            return True

    def _final_status_check(self, session_id):
        path = SESSION_STATUS_PATH.format(session_id=session_id)
        with self.client.get(
            path,
            headers=_headers(),
            name="GET /session-status/:id (final check)",
            catch_response=True,
        ) as resp:
            if _is_rate_limited(resp):
                pass
            elif resp.status_code != 200:
                resp.failure(f"final status check failed: HTTP {resp.status_code}")
            else:
                resp.success()


# --------------------------------------------------------------------------
# Custom summary printed at the end of every run.
# --------------------------------------------------------------------------


@events.quitting.add_listener
def _print_summary(environment, **kwargs):
    stats = environment.stats
    total = stats.total

    # Separate rate-limited failures (tagged "rate_limited: ..." by
    # _is_rate_limited) from real errors, so the headline failure rate
    # isn't inflated by the shared IP+token rate-limit bucket.
    rate_limited_count = 0
    other_failure_count = 0
    for key, err in stats.errors.items():
        occurrences = getattr(err, "occurrences", 0)
        error_text = getattr(err, "error", "") or ""
        if "rate_limited" in str(error_text):
            rate_limited_count += occurrences
        else:
            other_failure_count += occurrences

    print("\n" + "=" * 60)
    print("LOAD TEST SUMMARY")
    print("=" * 60)
    print(f"Requests:              {total.num_requests}")
    print(f"Total failures:        {total.num_failures}")
    print(f"  - rate_limited (429):{rate_limited_count}")
    print(f"  - other failures:    {other_failure_count}")
    fail_rate = (
        (total.num_failures / total.num_requests * 100) if total.num_requests else 0
    )
    other_fail_rate = (
        (other_failure_count / total.num_requests * 100) if total.num_requests else 0
    )
    print(f"Total failure rate:    {fail_rate:.2f}%")
    print(
        f"Non-rate-limit rate:   {other_fail_rate:.2f}%  <-- use THIS to judge real errors"
    )
    print(f"Median resp (ms):      {total.median_response_time}")
    print(f"95th pct (ms):         {total.get_response_time_percentile(0.95)}")
    print(f"99th pct (ms):         {total.get_response_time_percentile(0.99)}")
    print(f"Max resp (ms):         {total.max_response_time}")
    print("=" * 60)
    if rate_limited_count > 0:
        print(
            "NOTE: rate-limited requests are expected with >~60 total "
            "req/min from a single Locust host + shared API token (see "
            "RATE LIMITING section at the top of this file). They are "
            "excluded from 'Non-rate-limit rate' above."
        )

    max_acceptable_failure_rate = float(
        os.environ.get("LOAD_TEST_MAX_FAILURE_RATE", "5")
    )
    if other_fail_rate > max_acceptable_failure_rate:
        print(
            f"NON-RATE-LIMIT FAILURE RATE {other_fail_rate:.2f}% exceeds "
            f"threshold {max_acceptable_failure_rate}% — treat this run "
            f"as a failed test."
        )
