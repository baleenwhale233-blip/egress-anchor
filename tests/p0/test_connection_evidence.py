import unittest

from guest_client_probe import connection_chain


class ConnectionEvidenceTests(unittest.TestCase):
    def test_same_host_udp_and_old_tcp_are_not_current_tcp_evidence(self):
        def row(network, port, chain):
            return {"metadata": {"host": "managed.p0.invalid", "network": network, "sourcePort": port},
                    "chains": chain}
        rows = [row("tcp", "5001", ["correct"]), row("udp", "5001", ["DIRECT"]),
                row("tcp", "5002", ["old"])]
        self.assertEqual(["correct"], connection_chain(rows, "managed.p0.invalid", 5001))

    def test_missing_or_ambiguous_connections_cannot_be_a_success(self):
        row = {"metadata": {"host": "managed.p0.invalid", "network": "tcp", "sourcePort": "5001"},
               "chains": ["correct"]}
        for rows in (None, [], [row, row]):
            with self.subTest(rows=rows), self.assertRaises(AssertionError):
                connection_chain(rows, "managed.p0.invalid", 5001)


if __name__ == "__main__":
    unittest.main()
