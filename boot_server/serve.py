"""
This module implements a simple HTTP and TFTP server for booting machines.

It serves two directories under OUTPUT_DIR: 'tftp' over TFTP and 'http' over
HTTP. Everything it needs is read from the environment, so the same script runs
unchanged wherever the image in boot_server/Dockerfile is started -- on the
deployment host, or anywhere else on the nodes' network segment.
"""

import logging
import os
import signal
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import tftpy

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Configuration
OUTPUT_DIR = os.environ.get('OUTPUT_DIR', os.path.join(os.getcwd(), 'output'))
HTTP_ROOT = os.environ.get('HTTP_ROOT', os.path.join(OUTPUT_DIR, 'http'))
TFTP_ROOT = os.environ.get('TFTP_ROOT', os.path.join(OUTPUT_DIR, 'tftp'))
# Default to every interface: a container or a pod has no way of knowing which
# address the nodes will reach it on. `make serve` passes the one from
# ansible/inventory.yaml, which is also what the generated PXE menus name.
BIND_IP = os.environ.get('BIND_IP', '0.0.0.0')
HTTP_PORT = int(os.environ.get('HTTP_PORT', '8000'))
TFTP_PORT = int(os.environ.get('TFTP_PORT', '69'))

class CustomHTTPHandler(SimpleHTTPRequestHandler):
    """
    Custom HTTP request handler that serves files from HTTP_ROOT.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HTTP_ROOT, **kwargs)

def run_http(port):
    """
    Starts the HTTP server on the specified port.
    """
    # Threaded, because six nodes fetch a 370 MB initrd at the same time. A
    # single-threaded server hands it to them one after another, and the ones
    # still waiting time out in the firmware rather than in anything that logs.
    httpd = ThreadingHTTPServer((BIND_IP, port), CustomHTTPHandler)
    httpd.daemon_threads = True
    logger.info("Starting HTTP Server on %s:%s serving %s", BIND_IP, port, HTTP_ROOT)
    httpd.serve_forever()

def handle_sigterm(server):
    """
    Builds a SIGTERM handler that stops the TFTP server, and with it the
    process. A container runs this as PID 1, where the kernel drops signals
    that only have a default action -- without a handler installed, `docker
    stop` and every pod eviction wait out their grace period and then SIGKILL.
    """
    def handler(signum, _frame):
        logger.info("Received signal %s, shutting down", signum)
        server.stop(now=True)
    return handler

def main():
    """
    Serves TFTP_ROOT over TFTP and HTTP_ROOT over HTTP until signalled.
    """
    for directory in (HTTP_ROOT, TFTP_ROOT):
        os.makedirs(directory, exist_ok=True)

    http_thread = threading.Thread(target=run_http, args=(HTTP_PORT,), daemon=True)
    http_thread.start()

    server = tftpy.TftpServer(TFTP_ROOT)
    signal.signal(signal.SIGTERM, handle_sigterm(server))

    try:
        logger.info("Starting TFTP Server on %s:%s serving %s", BIND_IP, TFTP_PORT, TFTP_ROOT)
        server.listen(BIND_IP, TFTP_PORT)
    except PermissionError:
        # Exit non-zero: a process serving HTTP and not TFTP looks healthy and
        # boots nothing, which is the worst of the two failures.
        logger.error(
            "Permission denied to bind port %s. The container needs "
            "NET_BIND_SERVICE, or set TFTP_PORT to an unprivileged port.",
            TFTP_PORT
        )
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Failed to start TFTP: %s", e)
        sys.exit(1)

if __name__ == '__main__':
    main()
