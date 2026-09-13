"""
TFTP and HTTP boot server for the bare metal nodes.

Serves output/tftp and output/http, narrates what each node is collecting, and
switches a node's PXE menu back to local boot once it has the OS image -- so the
reboot at the end of an install boots the disk instead of the installer again.
"""

import logging
import os
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import tftpy
from tftpy.TftpContexts import TftpContextServer

TFTP_PORT = 69
HTTP_PORT = 8000
BIND_IP = '10.9.200.222'

HTTP_DIR = os.path.join(os.getcwd(), 'output', 'http')
TFTP_DIR = os.path.join(os.getcwd(), 'output', 'tftp')
PXE_DIR = os.path.join(TFTP_DIR, 'pxelinux.cfg')

OS_IMAGE = 'flatcar_production_image.bin.bz2'
MENU_NAME = re.compile(r'^01-[0-9a-f]{2}(?:-[0-9a-f]{2}){5}$', re.IGNORECASE)
MENU_HOST = re.compile(r'ignition-([^/\s]+)\.json')
IGNITION_NAME = re.compile(r'^ignition-(.+)\.json$')

logger = logging.getLogger('bootserver')
hosts_by_ip = {}
menu_lock = threading.Lock()


class Console(logging.Formatter):
    """Timestamp, who it is about, what it is doing."""

    def format(self, record):
        who = getattr(record, 'who', None)
        if who is None:
            who = 'tftp' if record.name.startswith('tftpy') else 'server'
        message = record.getMessage()
        if record.levelno > logging.INFO:
            message = f'{record.levelname.lower()}: {message}'
        return f'{self.formatTime(record, "%H:%M:%S")}  {who:<12}  {message}'


def say(who, message, *args, level=logging.INFO):
    """Log a line attributed to a node, or to the server itself."""
    logger.log(level, message, *args, extra={'who': who})


def human_size(path):
    """A parenthesised size, or nothing at all if the file is not there."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f' ({size:.0f} B)' if unit == 'B' else f' ({size:.1f} {unit})'
        size /= 1024
    return ''


def pxe_menus():
    """Menu filename -> (host, path), read back from what make config generated."""
    menus = {}
    try:
        names = os.listdir(PXE_DIR)
    except OSError:
        return menus
    for name in names:
        if not MENU_NAME.match(name):
            continue
        path = os.path.join(PXE_DIR, name)
        try:
            with open(path, encoding='utf-8') as menu:
                host = MENU_HOST.search(menu.read())
        except OSError:
            continue
        if host:
            menus[name] = (host.group(1), path)
    return menus


def pxe_default(path):
    """What the menu will boot if nobody touches the keyboard: install or localboot."""
    try:
        with open(path, encoding='utf-8') as menu:
            for line in menu:
                if line.startswith('DEFAULT '):
                    return line.split(None, 1)[1].strip()
    except OSError:
        pass
    return None


def switch_to_local_boot(host):
    """Rewrite DEFAULT install back to DEFAULT localboot. True if it was armed."""
    with menu_lock:
        path = next((p for h, p in pxe_menus().values() if h == host), None)
        if path is None or pxe_default(path) != 'install':
            return False
        with open(path, encoding='utf-8') as menu:
            lines = menu.readlines()
        with open(path, 'w', encoding='utf-8') as menu:
            menu.writelines(
                'DEFAULT localboot\n' if line.startswith('DEFAULT ') else line
                for line in lines
            )
    return True


class NarratingContext(TftpContextServer):  # pylint: disable=too-few-public-methods
    """tftpy offers no per-request hook; the session context is the narrowest wrap."""

    def start(self, buffer):
        """Handle the request as tftpy would, then say which node asked for what."""
        super().start(buffer)
        if self.file_to_transfer:
            announce_tftp(self.host, os.path.basename(self.file_to_transfer))


def announce_tftp(ip, name):
    """Narrate a TFTP request, and remember which node the client IP belongs to."""
    menu = pxe_menus().get(name)
    if menu:
        host, path = menu
        hosts_by_ip[ip] = host
        if pxe_default(path) == 'install':
            say(host, 'collecting its boot menu -- armed, so it will install')
        else:
            say(host, 'collecting its boot menu -- booting from its local disk')
    elif MENU_NAME.match(name):
        say(ip, 'asked for %s -- no generated menu has that MAC, check '
                'mac_address in inventory.yaml', name, level=logging.WARNING)
    else:
        say(hosts_by_ip.get(ip, ip), 'collecting the bootloader (%s)', name)


def describe(name, path):
    """A phrase for the file a node just asked for, in the words the docs use."""
    size = human_size(path)
    if name == f'{OS_IMAGE}.sig':
        return 'the OS image signature'
    if name == OS_IMAGE:
        return f'the OS image{size} -- this is the long one'
    if IGNITION_NAME.match(name):
        return 'its Ignition config'
    phrases = {
        '.vmlinuz': 'the kernel',
        '.cpio.gz': f'the initrd{size}',
        '.raw': f'the {name.split("-")[0]} sysext{size}',
    }
    for suffix, phrase in phrases.items():
        if name.endswith(suffix):
            return phrase
    return name


class BootHandler(SimpleHTTPRequestHandler):
    """Serves output/http and narrates each request against the node that made it."""

    def __init__(self, *args, **kwargs):
        self.status = None
        super().__init__(*args, directory=HTTP_DIR, **kwargs)

    def log_message(self, *args):
        """Silenced -- do_GET narrates instead."""

    def send_response(self, code, message=None):
        self.status = code
        super().send_response(code, message)

    def do_GET(self):
        relative = unquote(urlparse(self.path).path).lstrip('/')
        name = os.path.basename(relative) or '/'
        ip = self.client_address[0]
        named = IGNITION_NAME.match(name)
        if named:
            hosts_by_ip[ip] = named.group(1)
        host = hosts_by_ip.get(ip)

        self.status = 200
        say(host or ip, 'collecting %s',
            describe(name, os.path.join(HTTP_DIR, relative)))
        super().do_GET()

        if self.status != 200:
            say(host or ip, '%s is not in output/http -- run make artifacts',
                name, level=logging.WARNING)
        elif name == OS_IMAGE:
            self.disarm(host, ip)

    def disarm(self, host, ip):
        """The node has the image and is about to reboot: send it to its disk."""
        if host is None:
            say(ip, 'took the OS image but never asked for an Ignition config, '
                    'so I cannot tell which node it is -- run make '
                    'reinstall-cancel before it reboots', level=logging.WARNING)
        elif switch_to_local_boot(host):
            say(host, 'OS image delivered -- switching to local boot, so the '
                      'reboot lands on the disk')


def run_http():
    """Serve output/http until the process is killed."""
    os.makedirs(HTTP_DIR, exist_ok=True)
    try:
        ThreadingHTTPServer(('', HTTP_PORT), BootHandler).serve_forever()
    except OSError as error:
        say('server', 'HTTP failed to start on port %s: %s', HTTP_PORT, error,
            level=logging.ERROR)


def run_tftp():
    """Serve output/tftp until the process is killed. Blocks the main thread."""
    os.makedirs(TFTP_DIR, exist_ok=True)
    sys.modules['tftpy.TftpServer'].TftpContextServer = NarratingContext
    tftpy.TftpServer(TFTP_DIR).listen(BIND_IP, TFTP_PORT)


def announce_start():
    """Say where the servers are and which nodes are armed, before anything boots."""
    say('server', 'http on %s:%s from output/http', BIND_IP, HTTP_PORT)
    say('server', 'tftp on %s:%s from output/tftp', BIND_IP, TFTP_PORT)

    menus = pxe_menus()
    if not menus:
        say('server', 'no PXE menus in output/tftp/pxelinux.cfg -- run make '
                      'config, or no node can boot', level=logging.WARNING)
        return

    armed = sorted(h for h, p in menus.values() if pxe_default(p) == 'install')
    local = sorted(h for h, p in menus.values() if pxe_default(p) != 'install')
    if armed:
        say('server', 'armed to install: %s', ', '.join(armed))
    else:
        say('server', 'nothing is armed -- every menu says local boot, '
                      'make reinstall arms one')
    if local:
        say('server', 'booting from disk: %s', ', '.join(local))


if __name__ == '__main__':
    console = logging.StreamHandler()
    console.setFormatter(Console())
    logging.basicConfig(level=logging.INFO, handlers=[console])
    logging.getLogger('tftpy').setLevel(logging.WARNING)

    announce_start()

    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()

    try:
        run_tftp()
    except PermissionError:
        say('server', 'cannot bind port %s -- make serve needs sudo',
            TFTP_PORT, level=logging.ERROR)
    except Exception as error:  # pylint: disable=broad-exception-caught
        say('server', 'TFTP failed to start: %s', error, level=logging.ERROR)
