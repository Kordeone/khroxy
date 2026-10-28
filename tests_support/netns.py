"""Run named tests in a disposable rootless network namespace, loopback only.

Invoke with unshare --user --map-root-user --net python3 -m tests_support.netns MODULE.
This changes only the new namespace, never the host interface or firewall.
"""
import fcntl
import socket
import struct
import sys
import unittest

if __name__ == '__main__':
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        flags = fcntl.ioctl(sock.fileno(), 0x8913, struct.pack('16sH14x', b'lo', 0))
        value = struct.unpack('16sH14x', flags)[1]
        fcntl.ioctl(sock.fileno(), 0x8914, struct.pack('16sH14x', b'lo', value | 1))
    unittest.main(module=None, argv=[sys.argv[0], *sys.argv[1:]], verbosity=2)
