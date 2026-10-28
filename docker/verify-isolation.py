#!/usr/bin/env python3
"""Runtime, active bypass smoke test. Execute as the unprivileged agent user.

A failed direct socket is necessary, NOT sufficient, proof of the kill switch.
"""
import os
import socket
import sys

if os.geteuid() == 0:
    raise SystemExit('FAIL: do not run this test as root')

DESTS = [
    ('IPv4 public HTTPS', socket.AF_INET, ('1.1.1.1', 443)),
    ('IPv4 public DNS TCP', socket.AF_INET, ('8.8.8.8', 53)),
    ('IPv6 public HTTPS', socket.AF_INET6, ('2606:4700:4700::1111', 443, 0, 0)),
]

failures = []
for label, family, dest in DESTS:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(dest)
        print('FAIL: unproxied', label, 'connected directly')
        failures.append(label)
    except OSError as exc:
        print('BLOCKED:',label,'('+type(exc).__name__+')')

if failures:
    sys.exit(1)
print('PASS: direct-connect probes blocked. This smoke test does not prove all egress paths.')
