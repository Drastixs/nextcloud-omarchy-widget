import unittest

from support import fixture, fixture_jsonl
from ncstatus.speed import SpeedMeter, parse_ss


def sockets_of(sample):
    return {key: (row["s"], row["r"]) for key, row in sample["ss"].items()}


def run_sampler(name, step=2):
    """Feed 1 Hz fixture samples at the helper's 2 s cadence; collect smoothed readings."""
    meter = SpeedMeter()
    readings = []
    for sample in fixture_jsonl(name)[::step]:
        meter.add(sample["t"], sockets_of(sample))
        readings.append(meter.rates())
    return readings


class ParseSs(unittest.TestCase):
    def test_idle_fixture(self):
        sockets = parse_ss(fixture("ss-tinHp-client-idle.txt"), pid=1764)
        self.assertEqual(sockets["192.0.2.1:43748>192.0.2.1:443"], (1145481, 81484))
        self.assertEqual(sockets["192.0.2.1:53704>192.0.2.1:443"], (33405818, 20209352))
        self.assertEqual(len(sockets), 5)

    def test_filters_other_processes(self):
        text = ('ESTAB 0 0 10.0.0.1:1 10.0.0.2:443 users:(("firefox",pid=9,fd=1))\n'
                "\t cubic bytes_sent:100 bytes_received:200\n"
                'ESTAB 0 0 10.0.0.1:2 10.0.0.2:443 users:(("nextcloud",pid=7,fd=2))\n'
                "\t cubic bytes_sent:5\n")
        self.assertEqual(parse_ss(text), {"10.0.0.1:2>10.0.0.2:443": (5, 0)})
        self.assertEqual(parse_ss(text, pid=9), {"10.0.0.1:1>10.0.0.2:443": (100, 200)})
        self.assertEqual(parse_ss(text, pid=1), {})

    def test_row_with_no_counters_line_does_not_shift_pairs(self):
        text = ('ESTAB 0 0 a:1 b:2 users:(("nextcloud",pid=7,fd=2))\n'
                'ESTAB 0 0 a:3 b:4 users:(("firefox",pid=8,fd=2))\n'
                "\t cubic bytes_sent:100\n")
        self.assertEqual(parse_ss(text), {})

    def test_ipv6_addresses(self):
        text = ('ESTAB 0 0 [fd7a::1]:5 [fd7a::2]:443 users:(("nextcloud",pid=7,fd=2))\n'
                "\t bytes_sent:1 bytes_received:2\n")
        self.assertEqual(parse_ss(text), {"[fd7a::1]:5>[fd7a::2]:443": (1, 2)})


class Meter(unittest.TestCase):
    def test_first_sample_is_only_a_baseline(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (10 ** 9, 10 ** 9)})
        self.assertEqual(meter.rates(), (0, 0))

    def test_delta_over_time(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (0, 0)})
        meter.add(2, {"a": (2000, 4000)})
        self.assertEqual(meter.rates(), (1000, 2000))

    def test_new_socket_starts_from_zero(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (100, 100)})
        meter.add(2, {"a": (100, 100), "b": (2000, 0)})
        self.assertEqual(meter.rates(), (1000, 0))

    def test_closed_socket_never_goes_negative(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (10 ** 6, 10 ** 6), "b": (5, 5)})
        meter.add(2, {"b": (5, 5)})
        self.assertEqual(meter.rates(), (0, 0))

    def test_reused_address_pair_counts_from_zero(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (10 ** 6, 0)})
        meter.add(2, {"a": (2000, 0)})
        self.assertEqual(meter.rates(), (1000, 0))

    def test_below_floor_reads_zero_per_direction(self):
        meter = SpeedMeter()
        meter.add(0, {"a": (0, 0)})
        meter.add(2, {"a": (19_998, 40_000)})
        self.assertEqual(meter.rates(), (0, 20_000))

    def test_smooths_over_five_samples(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (0, 0)})
        total = 0
        for step in range(1, 8):
            total += 2000 if step % 2 else 0
            meter.add(step * 2, {"a": (total, 0)})
        self.assertEqual(len(meter.samples), 5)
        self.assertAlmostEqual(meter.rates()[0], 600, delta=1)  # 3 bursts in the last 10 s

    def test_long_gap_drops_the_baseline(self):
        meter = SpeedMeter(floor=0)
        meter.add(0, {"a": (0, 0)})
        meter.add(100, {"a": (10 ** 9, 0)})
        self.assertEqual(meter.rates(), (0, 0))


class Captured(unittest.TestCase):
    """Against real samples, with true rates from the spike's table."""

    def test_upload_20mb(self):
        true_rate = 20e6 / 43.66
        values = [up for up, _ in run_sampler("sampler-up20.jsonl")]
        moving = [v for v in values if v > 0]
        # Skip the window filling at the start and draining at the end.
        settled = moving[4:-4]
        self.assertGreater(len(settled), 5)
        for value in settled:
            self.assertLess(abs(value - true_rate) / true_rate, 0.20)

    def test_download_20mb(self):
        true_rate = 20e6 / 12.28
        peak = max(down for _, down in run_sampler("sampler-dl20.jsonl"))
        self.assertLess(abs(peak - true_rate) / true_rate, 0.20)

    def test_idle_stretch_reads_zero(self):
        self.assertIn((0, 0), run_sampler("sampler-m5.jsonl")[:3])


if __name__ == "__main__":
    unittest.main()
