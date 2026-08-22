# Load Test Results

## 1. Objective

Identify the actual system bottleneck under load and recommend how the system should scale.

## 2. Load Testing

The system was tested with **10, 50, 100, and 500 concurrent users** using realistic interview flows.

| Concurrent users | Requests | Error rate | Median response | p95 response | p99 response |
| ---------------- | -------- | ---------- | --------------- | ------------ | ------------ |
| 10               | 1,581    | 0.00%      | 97ms            | 450ms        | 680ms        |
| 50               | 2,936    | 0.10%      | 850ms           | 6,000ms      | 7,100ms      |
| 100              | 3,195    | 0.22%      | 2,300ms         | 16,000ms     | 18,000ms     |
| 500              | 3,472    | 0.58%      | 7,300ms         | 16,000ms     | 18,000ms     |

A monitored **100-user test** was also performed while observing CPU, memory, PostgreSQL connections, and Redis activity.

## 3. Findings

At 100 concurrent users:

- **91.22% of requests failed**
- Failures were mainly **HTTP 503** on `/start-interview`
- FastAPI CPU: **0.46%**
- FastAPI memory: **10.29%**
- PostgreSQL CPU: **3.26%**
- Database connections: **1–2 active**
- Redis: **~855 ops/sec peak**

These resources had significant available capacity, so **database, Redis, and CPU were not the bottleneck**.

## 4. Actual Bottleneck

The bottleneck is the **worker capacity**.

Only one worker was running with a capacity of **4**. Four sessions were assigned, after which no additional sessions were assigned because the worker's active-task count was not released after successful completion.

## 5. Scaling Recommendation

**First:** Fix the worker slot-release/completion handling.

**Then:** Increase worker capacity and/or add more worker instances.

Increasing capacity before fixing the release problem would only delay saturation.

## 6. Safe Capacity

With the current bug, the system can permanently saturate after approximately **4 sessions**.

After fixing the release issue, perform another monitored load test to determine the actual sustained safe concurrency.

## Conclusion

The main bottleneck is **worker capacity, not database, Redis, or compute resources**. The priority is to fix worker slot release, then scale workers and retest.
