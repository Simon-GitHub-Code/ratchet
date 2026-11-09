import unittest

from gatekeep.limiter import Limiter


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class LimiterTests(unittest.TestCase):
    def test_allows_up_to_the_limit(self):
        limiter = Limiter(3, 60, FakeClock(10))
        self.assertEqual([limiter.allow("a") for _ in range(4)], [True, True, True, False])

    def test_next_window_starts_again(self):
        clock = FakeClock(10)
        limiter = Limiter(1, 60, clock)
        self.assertTrue(limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        clock.now = 75
        self.assertTrue(limiter.allow("a"))

    def test_denied_requests_are_not_counted(self):
        clock = FakeClock(10)
        limiter = Limiter(1, 60, clock)
        limiter.allow("a")
        for _ in range(5):
            limiter.allow("a")
        clock.now = 70
        self.assertTrue(limiter.allow("a"))


if __name__ == "__main__":
    unittest.main()
