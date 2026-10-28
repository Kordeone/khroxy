"""Disposable process fixtures using explicitly supplied, trusted test engines."""
from contextlib import contextmanager
import os
import json
from pathlib import Path
import socket
import subprocess
import time
from khroxy_lib import engines


def free_port(udp=False):
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


@contextmanager
def process(binary,name,path,env=None,port=None):
    log_path=Path(str(path)+'.test-log')
    with log_path.open('wb') as log:
        child=subprocess.Popen(engines.command(binary,name,path),stdout=log,stderr=log,env=env,start_new_session=True)
        try:
            deadline=time.monotonic()+6
            checks=[('127.0.0.1',port)] if port is not None else []
            management=json.loads(Path(path).read_text()).get('experimental',{}).get('clash_api',{}).get('external_controller')
            if management:
                host,control_port=management.rsplit(':',1);checks.append((host,int(control_port)))
            while True:
                if child.poll() is not None: raise AssertionError('Test engine exited: '+log_path.read_text())
                if not checks:
                    time.sleep(.15); break
                try:
                    for address in checks:
                        with socket.create_connection(address,timeout=.1):pass
                    break
                except OSError:
                    if time.monotonic()>deadline: raise AssertionError('Test engine did not listen: '+log_path.read_text())
                    time.sleep(.03)
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=4)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=4)


def paths():
    return {name:Path(os.environ[key]) for name,key in [('xray','KHROXY_TEST_XRAY'),('v2ray','KHROXY_TEST_V2RAY'),('sing-box','KHROXY_TEST_SING_BOX')] if os.environ.get(key)}
