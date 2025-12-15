import threading
import logging
import os
import tftpy
from http.server import SimpleHTTPRequestHandler, HTTPServer

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Constants
TFTP_PORT = 69
HTTP_PORT = 8000
BIND_IP = '10.9.200.222'

class CustomHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Serve from output/http relative to current working directory
        super().__init__(*args, directory=os.path.join(os.getcwd(), 'output', 'http'), **kwargs)

def run_http(port):
    server_address = ('', port)
    # Ensure directory exists
    http_dir = os.path.join(os.getcwd(), 'output', 'http')
    os.makedirs(http_dir, exist_ok=True)
    httpd = HTTPServer(server_address, CustomHTTPHandler)
    logger.info(f"Starting HTTP Server on port {port} serving {http_dir}")
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
        
        logger.info(f"Starting TFTP Server on {BIND_IP}:{TFTP_PORT} serving {tftp_root}")
        server = tftpy.TftpServer(tftp_root)
        server.listen(BIND_IP, TFTP_PORT)
    except PermissionError:
        logger.error(f"Permission denied to bind port {TFTP_PORT}. Try running with sudo or change port.")
    except Exception as e:
        logger.error(f"Failed to start TFTP: {e}")
