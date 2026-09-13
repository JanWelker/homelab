"""
TFTP and HTTP boot server for the bare metal nodes.

Serves output/tftp and output/http, narrates what each node is collecting, and
switches a node's PXE menu back to local boot once it has the OS image -- so the
reboot at the end of an install boots the disk instead of the installer again.
"""

import json
import logging
import os
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import tftpy
from tftpy.TftpContexts import TftpContextServer

TFTP_PORT = 69
HTTP_PORT = 8000
BIND_IP = '10.9.200.222'

HTTP_DIR = os.path.join(os.getcwd(), 'output', 'http')
TFTP_DIR = os.path.join(os.getcwd(), 'output', 'tftp')
MENU_SUBDIR = 'pxelinux.cfg'
PXE_DIR = os.path.join(TFTP_DIR, MENU_SUBDIR)

OS_IMAGE = 'flatcar_production_image.bin.bz2'
MENU_NAME = re.compile(r'^01-[0-9a-f]{2}(?:-[0-9a-f]{2}){5}$', re.IGNORECASE)
MENU_HOST = re.compile(r'ignition-([^/\s]+)\.json')
IGNITION_NAME = re.compile(r'^ignition-(.+)\.json$')

# What a PXE client does on the way in that tftpy reports as a failure.
TFTP_NOISE = (
    # UEFI firmware asks for TFTP options, is refused, aborts and retries
    # without them. RFC 2347 errorcode 8 is that handshake, not a failure.
    re.compile(r'errorcode[ :=]+8\b'),
    # PXELINUX works down a search list -- client UUID, then 01-<mac>, then
    # the IP in hex, then 'default' -- so misses are how it finds the menu.
    # announce_tftp reports the one miss that matters, and says what to do.
    re.compile(r'File not found: .*[/\\]' + MENU_SUBDIR + r'[/\\]'),
)

# role, name, address, MAC -- the width the identity column is padded to.
COLUMNS = (13, 8, 15, 17)
WHO_WIDTH = sum(COLUMNS) + len(COLUMNS) - 1

logger = logging.getLogger('bootserver')
hosts_by_ip = {}
roles_by_host = {}
menu_lock = threading.Lock()


class DropKnownTftpNoise(logging.Filter):  # pylint: disable=too-few-public-methods
    """Keep tftpy's warnings and errors, minus the ones every PXE boot causes."""

    def filter(self, record):
        if not record.name.startswith('tftpy'):
            return True
        message = record.getMessage()
        return not any(noise.search(message) for noise in TFTP_NOISE)


class Console(logging.Formatter):
    """Timestamp, who it is about, what it is doing."""

    def format(self, record):
        who = getattr(record, 'who', None)
        if who is None:
            who = 'tftp' if record.name.startswith('tftpy') else 'server'
        message = record.getMessage()
        if record.levelno > logging.INFO:
            message = f'{record.levelname.lower()}: {message}'
        return f'{self.formatTime(record, "%H:%M:%S")}  {who:<{WHO_WIDTH}}  {message}'


def node_role(host):
    """control-plane or worker, read out of the config generated for that host."""
    if host not in roles_by_host:
        role = None
        try:
            with open(os.path.join(HTTP_DIR, f'ignition-{host}.json'),
                      encoding='utf-8') as config:
                units = json.load(config).get('systemd', {}).get('units', [])
            unit = next(u for u in units if u['name'] == 'bootstrap-k8s.service')
            contents = unit.get('contents', '')
            role = ('control-plane'
                    if 'kubeadm init' in contents or '--control-plane' in contents
                    else 'worker')
        except (OSError, ValueError, KeyError, StopIteration):
            pass
        roles_by_host[host] = role
    return roles_by_host[host]


def node_mac(host):
    """The MAC the node's menu is named after, colon-separated as the inventory has it."""
    for name, (menu_host, _) in pxe_menus().items():
        if menu_host == host:
            return name[len('01-'):].replace('-', ':')
    return None


def identity(ip):
    """The who column: role, name, address and MAC, as far as they are known."""
    host = hosts_by_ip.get(ip)
    fields = (node_role(host) if host else None, host, ip,
              node_mac(host) if host else None)
    return ' '.join(f'{value or "-":<{width}}'
                    for value, width in zip(fields, COLUMNS)).rstrip()


def say(ip, message, *args, level=logging.INFO):
    """Log a line against whoever made the request, or against the server itself."""
    who = ip if ip == 'server' else identity(ip)
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
        try:
            super().start(buffer)
        finally:
            # A missing file raises, and that request is the one worth naming.
            if self.file_to_transfer:
                announce_tftp(self.host, self.file_to_transfer)


def announce_tftp(ip, requested):
    """Narrate a TFTP request, and remember which node the client IP belongs to."""
    name = os.path.basename(requested)
    menu = pxe_menus().get(name)
    if menu:
        host, path = menu
        hosts_by_ip[ip] = host
        if pxe_default(path) == 'install':
            say(ip, 'collecting its boot menu -- armed, so it will install')
        else:
            say(ip, 'collecting its boot menu -- booting from its local disk')
    elif MENU_NAME.match(name):
        say(ip, 'asked for %s -- no generated menu has that MAC, check '
                'mac_address in inventory.yaml', name, level=logging.WARNING)
    elif os.path.basename(os.path.dirname(requested)) != MENU_SUBDIR:
        say(ip, 'collecting the bootloader (%s)', name)


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
        # translate_path is what confines a request to output/http; going
        # around it to name the file would let a crafted URL stat anything.
        served = self.translate_path(self.path)
        name = '/' if os.path.isdir(served) else os.path.basename(served)
        ip = self.client_address[0]
        named = IGNITION_NAME.match(name)
        if named:
            hosts_by_ip[ip] = named.group(1)

        self.status = 200
        say(ip, 'collecting %s', describe(name, served))
        super().do_GET()

        if self.status != 200:
            say(ip, '%s is not in output/http -- run make artifacts',
                name, level=logging.WARNING)
        elif name == OS_IMAGE:
            self.disarm(ip)

    def disarm(self, ip):
        """The node has the image and is about to reboot: send it to its disk."""
        host = hosts_by_ip.get(ip)
        if host is None:
            say(ip, 'took the OS image but never asked for an Ignition config, '
                    'so I cannot tell which node it is -- run make '
                    'reinstall-cancel before it reboots', level=logging.WARNING)
        elif switch_to_local_boot(host):
            say(ip, 'OS image delivered -- switching to local boot, so the '
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
    console.addFilter(DropKnownTftpNoise())
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
