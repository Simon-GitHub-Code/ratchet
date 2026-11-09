# Request limiter

`Limiter(limit, window_seconds, clock)` decides whether a caller identified by `key` may make a request now. `clock()` returns the current time in seconds as a float.

1. Time is cut into fixed windows. Window `k` covers `[k * window_seconds, (k + 1) * window_seconds)`.
2. `allow(key)` returns `True`, and counts the request, while `key` has had fewer than `limit` allowed requests in the current window. Otherwise it returns `False`.
3. A request at exactly `(k + 1) * window_seconds` belongs to window `k + 1`: its count starts again from zero.
4. Keys are independent. One key using up its allowance never changes the answer for another key.
5. A denied request is not counted.
