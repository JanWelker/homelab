"""
This module implements a simple HTTP and TFTP server for booting machines.
It serves files from the 'output/http' and 'output/tftp' directories.
"""

import threading
import logging
import os
from http.server import SimpleHTTPRequestHandler, HTTPServer
import tftpy

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Constants
TFTP_PORT = 69
HTTP_PORT = 8000
BIND_IP = '10.9.200.222'

class CustomHTTPHandler(SimpleHTTPRequestHandler):
    """
    Custom HTTP request handler that serves files from the 'output/http' directory.
    """
    def __init__(self, *args, **kwargs):
        # Serve from output/http relative to current working directory
        super().__init__(*args, directory=os.path.join(os.getcwd(), 'output', 'http'), **kwargs)

def run_http(port):
    """
    Starts the HTTP server on the specified port.
    """
    server_address = ('', port)
    # Ensure directory exists
    http_dir = os.path.join(os.getcwd(), 'output', 'http')
    os.makedirs(http_dir, exist_ok=True)
    httpd = HTTPServer(server_address, CustomHTTPHandler)
    logger.info("Starting HTTP Server on port %s serving %s", port, http_dir)
    httpd.serve_forever()

if __name__ == '__main__':
    # Start HTTP
    http_thread = threading.Thread(target=run_http, args=(HTTP_PORT,))
    http_thread.daemon = True
    http_thread.start()

    # Start TFTP
    try:
        tftp_root = os.path.join(os.getcwd(), 'output', 'tftp')
        os.makedirs(tftp_root, exist_ok=True)

        logger.info("Starting TFTP Server on %s:%s serving %s", BIND_IP, TFTP_PORT, tftp_root)
        server = tftpy.TftpServer(tftp_root)
        server.listen(BIND_IP, TFTP_PORT)
    except PermissionError:
        logger.error(
            "Permission denied to bind port %s. Try running with sudo or change port.",
            TFTP_PORT
        )
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Failed to start TFTP: %s", e)
